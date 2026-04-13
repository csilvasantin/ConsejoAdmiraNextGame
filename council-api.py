"""
Council API Bridge — Conecta el frontend SCUMM con los agentes del Consejo AdmiraNext.
Usa FastAPI + Anthropic SDK para que cada consejero responda como su persona.

Seguridad:
  - COUNCIL_API_TOKEN: token que el frontend debe enviar en header X-Council-Token
  - CORS restringido a orígenes autorizados
  - Rate limiting por IP (máx peticiones por ventana de tiempo)
  - Cloudflare Tunnel para HTTPS sin abrir puertos
  - Presupuesto máximo de €20 con alertas por Telegram y email
"""

import sys
import os
import json
import asyncio
import time
import smtplib
import random
import threading
from typing import Optional
from collections import defaultdict
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add admiranext to path
sys.path.insert(0, os.path.expanduser("~/GitHub/admiranext"))

from admiranext.agents.base import CouncilAgent
from admiranext.agents.racional.leyendas import CEO, CTO, COO, CFO
from admiranext.agents.racional.coetaneos import (
    CEO_Coetaneo, CTO_Coetaneo, COO_Coetaneo, CFO_Coetaneo,
)
from admiranext.agents.creativo.leyendas import CCO, CDO, CXO, CSO
from admiranext.agents.creativo.coetaneos import (
    CCO_Coetaneo, CDO_Coetaneo, CXO_Coetaneo, CSO_Coetaneo,
)

import anthropic

try:
    import requests as http_requests
except ImportError:
    http_requests = None

# ── Config ──────────────────────────────────────────────────
COUNCIL_API_TOKEN = os.environ.get("COUNCIL_API_TOKEN", "")
ALLOWED_ORIGINS = [
    "https://csilvasantin.github.io",
    "http://localhost:8080",
    "http://localhost:3000",
    "http://127.0.0.1:8080",
]

# Rate limiting: max requests per IP per window
RATE_LIMIT_MAX = 10       # max requests
RATE_LIMIT_WINDOW = 300   # per 5 minutes

# ── Budget config ────────────────────────────────────────────
# Claude Sonnet 4 pricing (USD per token)
PRICE_INPUT_PER_TOKEN = 3.0 / 1_000_000    # $3 per 1M input tokens
PRICE_OUTPUT_PER_TOKEN = 15.0 / 1_000_000  # $15 per 1M output tokens
USD_TO_EUR = 0.92  # Conservative conversion rate

BUDGET_LIMIT_EUR = 20.0        # Hard cap in euros
BUDGET_WARN_PCT = 0.80         # Alert at 80% = €16
BUDGET_CRITICAL_PCT = 0.95     # Critical alert at 95% = €19

# Alert config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8753533419:AAHrZSbJhYZu4EZjCw7HSFuv4p-vactPTvc")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8663681")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "csilvasantin@gmail.com")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")

# Budget tracking file
BUDGET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "budget.json")

# ── Budget tracker ───────────────────────────────────────────
_budget_lock = threading.Lock()
_alert_sent = {"warn": False, "critical": False, "blocked": False}


def _load_budget() -> dict:
    """Load budget tracking data from disk."""
    if Path(BUDGET_FILE).exists():
        try:
            with open(BUDGET_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": 0.0,
        "total_cost_eur": 0.0,
        "total_requests": 0,
        "history": [],
        "alerts_sent": [],
        "created": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat(),
    }


def _save_budget(data: dict):
    """Save budget tracking data to disk."""
    data["last_updated"] = datetime.now().isoformat()
    with open(BUDGET_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def track_usage(input_tokens: int, output_tokens: int, agent_name: str):
    """Track API token usage and check budget limits."""
    with _budget_lock:
        budget = _load_budget()

        cost_usd = (input_tokens * PRICE_INPUT_PER_TOKEN) + (output_tokens * PRICE_OUTPUT_PER_TOKEN)
        cost_eur = cost_usd * USD_TO_EUR

        budget["total_input_tokens"] += input_tokens
        budget["total_output_tokens"] += output_tokens
        budget["total_cost_usd"] += cost_usd
        budget["total_cost_eur"] += cost_eur
        budget["total_requests"] += 1

        # Keep last 100 entries in history
        budget["history"].append({
            "timestamp": datetime.now().isoformat(),
            "agent": agent_name,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
            "cost_eur": round(cost_eur, 6),
        })
        if len(budget["history"]) > 100:
            budget["history"] = budget["history"][-100:]

        _save_budget(budget)

        # Check thresholds and send alerts
        total_eur = budget["total_cost_eur"]
        _check_alerts(total_eur, budget)


def _check_alerts(total_eur: float, budget: dict):
    """Check budget thresholds and fire alerts."""
    pct = total_eur / BUDGET_LIMIT_EUR

    if pct >= BUDGET_CRITICAL_PCT and not _alert_sent["critical"]:
        _alert_sent["critical"] = True
        msg = (
            f"🚨 CRÍTICO — Presupuesto Consejo AdmiraNext al {pct*100:.1f}%\n"
            f"Gastado: €{total_eur:.2f} / €{BUDGET_LIMIT_EUR:.2f}\n"
            f"Tokens: {budget['total_input_tokens']:,} in + {budget['total_output_tokens']:,} out\n"
            f"Peticiones: {budget['total_requests']}\n\n"
            f"⚠️ El servicio se bloqueará al llegar a €{BUDGET_LIMIT_EUR:.0f}.\n"
            f"Añade más crédito para continuar."
        )
        _fire_alerts(msg, "critical", budget)

    elif pct >= BUDGET_WARN_PCT and not _alert_sent["warn"]:
        _alert_sent["warn"] = True
        msg = (
            f"⚠️ AVISO — Presupuesto Consejo AdmiraNext al {pct*100:.1f}%\n"
            f"Gastado: €{total_eur:.2f} / €{BUDGET_LIMIT_EUR:.2f}\n"
            f"Tokens: {budget['total_input_tokens']:,} in + {budget['total_output_tokens']:,} out\n"
            f"Peticiones: {budget['total_requests']}\n\n"
            f"Considera añadir más crédito pronto."
        )
        _fire_alerts(msg, "warn", budget)


def _fire_alerts(message: str, level: str, budget: dict):
    """Send alerts via Telegram and email (non-blocking)."""
    budget["alerts_sent"].append({
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "cost_eur": round(budget["total_cost_eur"], 4),
    })
    _save_budget(budget)

    # Send in background threads to not block the API
    threading.Thread(target=_send_telegram, args=(message,), daemon=True).start()
    threading.Thread(target=_send_email, args=(message, level), daemon=True).start()
    print(f"🚨 Budget alert ({level}): €{budget['total_cost_eur']:.2f} / €{BUDGET_LIMIT_EUR:.2f}")


def _send_telegram(message: str):
    """Send alert to Telegram Bot Memorizer."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram not configured, skipping alert")
        return
    try:
        if http_requests:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            http_requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
            }, timeout=10)
            print("✅ Telegram alert sent")
        else:
            # Fallback: use urllib if requests not installed
            import urllib.request
            import urllib.parse
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = json.dumps({
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            print("✅ Telegram alert sent (urllib)")
    except Exception as e:
        print(f"❌ Telegram alert failed: {e}")


def _send_email(message: str, level: str):
    """Send alert email to csilvasantin@gmail.com."""
    if not SMTP_USER or not SMTP_PASS:
        print("⚠️ SMTP not configured, skipping email alert")
        return
    try:
        subject = f"{'🚨 CRÍTICO' if level == 'critical' else '⚠️ AVISO'} — Presupuesto Consejo AdmiraNext"
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = ALERT_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(message, "plain", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, ALERT_EMAIL, msg.as_string())
        print("✅ Email alert sent")
    except Exception as e:
        print(f"❌ Email alert failed: {e}")


def check_budget():
    """Check if budget is exceeded. Raises 402 if so."""
    budget = _load_budget()
    if budget["total_cost_eur"] >= BUDGET_LIMIT_EUR:
        if not _alert_sent["blocked"]:
            _alert_sent["blocked"] = True
            msg = (
                f"🛑 BLOQUEADO — Presupuesto Consejo AdmiraNext AGOTADO\n"
                f"Gastado: €{budget['total_cost_eur']:.2f} / €{BUDGET_LIMIT_EUR:.2f}\n"
                f"El servicio ha sido desactivado automáticamente.\n\n"
                f"Para reactivar, añade crédito y aumenta BUDGET_LIMIT_EUR en .env"
            )
            threading.Thread(target=_send_telegram, args=(msg,), daemon=True).start()
            threading.Thread(target=_send_email, args=(msg, "blocked"), daemon=True).start()
        raise HTTPException(
            status_code=402,
            detail=f"Budget exceeded: €{budget['total_cost_eur']:.2f} / €{BUDGET_LIMIT_EUR:.2f}. Service paused.",
        )


# ── App ──────────────────────────────────────────────────────
app = FastAPI(title="AdmiraNext Council API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "X-Council-Token"],
)

# ── Rate limiter ─────────────────────────────────────────────
_rate_store: dict = defaultdict(list)


def check_rate_limit(request: Request):
    """Simple in-memory rate limiter by IP."""
    ip = request.headers.get("cf-connecting-ip",
         request.headers.get("x-forwarded-for",
         request.client.host if request.client else "unknown"))
    now = time.time()
    _rate_store[ip] = [t for t in _rate_store[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_store[ip]) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit: max {RATE_LIMIT_MAX} requests per {RATE_LIMIT_WINDOW}s. Try again later.",
        )
    _rate_store[ip].append(now)


def verify_token(request: Request):
    """Verify the API token from the X-Council-Token header."""
    if not COUNCIL_API_TOKEN:
        return
    token = request.headers.get("x-council-token", "")
    if token != COUNCIL_API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid or missing API token")


# ── Shared Anthropic client ─────────────────────────────────
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ── Agent registry ───────────────────────────────────────────
AGENTS = {
    "leyendas": {
        "racional": [CEO, CTO, COO, CFO],
        "creativo": [CCO, CDO, CXO, CSO],
    },
    "coetaneos": {
        "racional": [CEO_Coetaneo, CTO_Coetaneo, COO_Coetaneo, CFO_Coetaneo],
        "creativo": [CCO_Coetaneo, CDO_Coetaneo, CXO_Coetaneo, CSO_Coetaneo],
    },
}

_agent_cache: dict = {}


def get_agent(cls) -> CouncilAgent:
    key = f"{cls.__module__}.{cls.__name__}"
    if key not in _agent_cache:
        _agent_cache[key] = cls(client=client)
    return _agent_cache[key]


# ── Models ───────────────────────────────────────────────────
class AskRequest(BaseModel):
    message: str
    generation: str = "leyendas"
    context: Optional[list] = None


class AskOneRequest(BaseModel):
    message: str
    agent_name: str  # e.g. "CEO", "CTO", "CCO"...
    generation: str = "leyendas"
    context: Optional[list] = None


class AgentReply(BaseModel):
    name: str
    role: str
    persona: str
    side: str
    icon: str
    content: str


class AskResponse(BaseModel):
    racional: list
    creativo: list


ICONS = {
    "CEO": "🏛️", "CTO": "⚙️", "COO": "📋", "CFO": "💰",
    "CCO": "💡", "CDO": "🎨", "CXO": "🌐", "CSO": "📖",
}

MAX_MESSAGE_LENGTH = 1000


def agent_ask(agent: CouncilAgent, message: str, context: Optional[list]) -> tuple:
    """Call Claude with the agent's persona. Returns (text, input_tokens, output_tokens)."""
    messages = []

    if context:
        for msg in context[-6:]:
            messages.append({
                "role": msg.get("role", "user"),
                "content": str(msg.get("content", ""))[:MAX_MESSAGE_LENGTH],
            })

    messages.append({"role": "user", "content": message[:MAX_MESSAGE_LENGTH]})

    conv_system = (
        agent.system_prompt + "\n\n"
        "INSTRUCCIONES DE CONVERSACIÓN:\n"
        "- Respondes directamente a la pregunta o comentario del usuario.\n"
        "- Sé conciso: máximo 2-3 frases.\n"
        "- Mantén tu personalidad y perspectiva única.\n"
        "- Si hay otros consejeros en la conversación, puedes referirte a ellos.\n"
        "- Usa tu experiencia y filosofía para dar respuestas genuinas.\n"
    )

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        system=conv_system,
        messages=messages,
    )

    # Extract token usage from response
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    return response.content[0].text, input_tokens, output_tokens


def _send_query_report(question: str, replies: list, cost_eur: float, gen: str):
    """Send a usage report with full responses to Telegram for historical record."""
    responses_txt = "\n\n".join(
        f"💬 *{r.icon} {r.name} ({r.persona})*:\n{r.content}"
        for r in replies
    )
    budget = _load_budget()
    msg = (
        f"📋 *Consejo AdmiraNext — Consulta*\n\n"
        f"❓ _{question[:300]}_\n\n"
        f"{responses_txt}\n\n"
        f"───────────────\n"
        f"💰 Coste: €{cost_eur:.4f} · "
        f"Acumulado: €{budget['total_cost_eur']:.4f} / €{BUDGET_LIMIT_EUR:.2f} "
        f"({budget['total_cost_eur']/BUDGET_LIMIT_EUR*100:.1f}%) · {gen}"
    )
    threading.Thread(target=_send_telegram, args=(msg,), daemon=True).start()


# ── Endpoints ────────────────────────────────────────────────
@app.post("/api/council/ask", response_model=AskResponse)
async def council_ask(
    req: AskRequest,
    _rate=Depends(check_rate_limit),
    _auth=Depends(verify_token),
):
    """Send a message to the council. 1 racional + 1 creativo (random) by default."""
    check_budget()

    gen = req.generation if req.generation in AGENTS else "leyendas"
    group = AGENTS[gen]
    loop = asyncio.get_event_loop()

    # Pick 1 random agent from each side
    racional_cls = random.choice(group["racional"])
    creativo_cls = random.choice(group["creativo"])
    selected = [racional_cls, creativo_cls]

    cost_before = _load_budget()["total_cost_eur"]

    async def run_agent(cls):
        agent = get_agent(cls)
        content, inp_tok, out_tok = await loop.run_in_executor(
            None, agent_ask, agent, req.message, req.context
        )
        track_usage(inp_tok, out_tok, agent.name)
        return AgentReply(
            name=agent.name,
            role=agent.role,
            persona=agent.persona,
            side=agent.side,
            icon=ICONS.get(agent.name, "🎯"),
            content=content,
        )

    # Run both agents in parallel (only 2 calls — no batching needed)
    tasks = [run_agent(cls) for cls in selected]
    all_replies = await asyncio.gather(*tasks)

    racional_replies = [r for r in all_replies if r.side == "racional"]
    creativo_replies = [r for r in all_replies if r.side == "creativo"]

    # Calculate cost of this query and send report to Telegram
    cost_after = _load_budget()["total_cost_eur"]
    query_cost = cost_after - cost_before
    _send_query_report(req.message, list(all_replies), query_cost, gen)

    return AskResponse(racional=racional_replies, creativo=creativo_replies)


@app.post("/api/council/ask-one")
async def council_ask_one(
    req: AskOneRequest,
    _rate=Depends(check_rate_limit),
    _auth=Depends(verify_token),
):
    """Ask a single specific agent. Used by 'Preguntar' verb."""
    check_budget()

    gen = req.generation if req.generation in AGENTS else "leyendas"
    group = AGENTS[gen]
    all_classes = list(group["racional"]) + list(group["creativo"])

    # Find the requested agent by name
    target_cls = None
    for cls in all_classes:
        agent = get_agent(cls)
        if agent.name == req.agent_name:
            target_cls = cls
            break

    if not target_cls:
        raise HTTPException(status_code=404, detail=f"Agent '{req.agent_name}' not found in {gen}")

    agent = get_agent(target_cls)
    cost_before = _load_budget()["total_cost_eur"]

    loop = asyncio.get_event_loop()
    content, inp_tok, out_tok = await loop.run_in_executor(
        None, agent_ask, agent, req.message, req.context
    )
    track_usage(inp_tok, out_tok, agent.name)

    reply = AgentReply(
        name=agent.name,
        role=agent.role,
        persona=agent.persona,
        side=agent.side,
        icon=ICONS.get(agent.name, "🎯"),
        content=content,
    )

    # Report to Telegram
    cost_after = _load_budget()["total_cost_eur"]
    query_cost = cost_after - cost_before
    _send_query_report(req.message, [reply], query_cost, gen)

    return reply


@app.get("/api/council/health")
async def health():
    budget = _load_budget()
    return {
        "status": "ok",
        "agents": 16,
        "security": {
            "token_required": bool(COUNCIL_API_TOKEN),
            "cors_origins": ALLOWED_ORIGINS,
            "rate_limit": f"{RATE_LIMIT_MAX} req / {RATE_LIMIT_WINDOW}s",
        },
        "budget": {
            "spent_eur": round(budget["total_cost_eur"], 4),
            "limit_eur": BUDGET_LIMIT_EUR,
            "remaining_eur": round(BUDGET_LIMIT_EUR - budget["total_cost_eur"], 4),
            "pct_used": round(budget["total_cost_eur"] / BUDGET_LIMIT_EUR * 100, 1),
            "total_requests": budget["total_requests"],
            "total_tokens": budget["total_input_tokens"] + budget["total_output_tokens"],
        },
    }


@app.get("/api/council/budget")
async def budget_status(request: Request, _auth=Depends(verify_token)):
    """Detailed budget status."""
    budget = _load_budget()
    return {
        "total_cost_eur": round(budget["total_cost_eur"], 4),
        "total_cost_usd": round(budget["total_cost_usd"], 4),
        "limit_eur": BUDGET_LIMIT_EUR,
        "remaining_eur": round(BUDGET_LIMIT_EUR - budget["total_cost_eur"], 4),
        "pct_used": round(budget["total_cost_eur"] / BUDGET_LIMIT_EUR * 100, 1),
        "total_input_tokens": budget["total_input_tokens"],
        "total_output_tokens": budget["total_output_tokens"],
        "total_requests": budget["total_requests"],
        "alerts_sent": budget.get("alerts_sent", []),
        "last_updated": budget.get("last_updated", ""),
        "warn_at_eur": round(BUDGET_LIMIT_EUR * BUDGET_WARN_PCT, 2),
        "critical_at_eur": round(BUDGET_LIMIT_EUR * BUDGET_CRITICAL_PCT, 2),
    }


# ── Run ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    budget = _load_budget()
    print("🏛️  AdmiraNext Council API v3.0 — http://localhost:8420")
    print(f"🔐 Token required: {bool(COUNCIL_API_TOKEN)}")
    print(f"🌐 Allowed origins: {ALLOWED_ORIGINS}")
    print(f"⏱️  Rate limit: {RATE_LIMIT_MAX} req / {RATE_LIMIT_WINDOW}s")
    print(f"💰 Budget: €{budget['total_cost_eur']:.4f} / €{BUDGET_LIMIT_EUR:.2f} "
          f"({budget['total_cost_eur']/BUDGET_LIMIT_EUR*100:.1f}% used)")
    print(f"📱 Telegram alerts: {'✅' if TELEGRAM_BOT_TOKEN else '❌'}")
    print(f"📧 Email alerts: {'✅' if SMTP_USER else '⚠️ Set SMTP_USER/SMTP_PASS in .env'}")
    uvicorn.run(app, host="0.0.0.0", port=8420)
