"""
Council API Bridge — Conecta el frontend SCUMM con los agentes del Consejo AdmiraNext.
Usa FastAPI + Anthropic SDK + Groq (modelos gratuitos) para que cada consejero responda.

Seguridad:
  - COUNCIL_API_TOKEN: token que el frontend debe enviar en header X-Council-Token
  - CORS restringido a orígenes autorizados
  - Rate limiting por IP (máx peticiones por ventana de tiempo)
  - Cloudflare Tunnel para HTTPS sin abrir puertos
  - Presupuesto máximo de €20 con alertas por Telegram y email

Modelos LLM:
  - Claude Sonnet 4 (Anthropic) — de pago, máxima calidad
  - Llama 3.3 70B (Groq) — gratuito
  - DeepSeek R1 (Groq) — gratuito
  - Gemma 2 9B (Groq) — gratuito
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
from fastapi.staticfiles import StaticFiles
import urllib.parse
import subprocess
import unicodedata
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

# ── LLM Models registry ──────────────────────────────────────
LLM_MODELS = {
    "claude-sonnet": {
        "name": "Claude Sonnet 4",
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-20250514",
        "free": False,
        "icon": "💎",
    },
    "llama-70b": {
        "name": "Llama 3.3 70B",
        "provider": "groq",
        "model_id": "llama-3.3-70b-versatile",
        "free": True,
        "icon": "🦙",
    },
    "deepseek-r1": {
        "name": "DeepSeek R1",
        "provider": "groq",
        "model_id": "deepseek-r1-distill-llama-70b",
        "free": True,
        "icon": "🔮",
    },
    "gemma-9b": {
        "name": "Gemma 2 9B",
        "provider": "groq",
        "model_id": "gemma2-9b-it",
        "free": True,
        "icon": "🌀",
    },
}

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

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


def track_usage(input_tokens: int, output_tokens: int, agent_name: str, llm_key: str = "claude-sonnet"):
    """Track API token usage and check budget limits."""
    model_cfg = LLM_MODELS.get(llm_key, LLM_MODELS["claude-sonnet"])
    is_free = model_cfg.get("free", False)

    with _budget_lock:
        budget = _load_budget()

        # Free models cost nothing
        if is_free:
            cost_usd = 0.0
            cost_eur = 0.0
        else:
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
            "llm": llm_key,
            "llm_name": model_cfg["name"],
            "free": is_free,
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
app = FastAPI(title="AdmiraNext Council API", version="4.0.0")

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
    llm: str = "claude-sonnet"  # LLM model key from LLM_MODELS


class AskOneRequest(BaseModel):
    message: str
    agent_name: str  # e.g. "CEO", "CTO", "CCO"...
    generation: str = "leyendas"
    context: Optional[list] = None
    llm: str = "claude-sonnet"  # LLM model key from LLM_MODELS


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


def _build_conversation(agent: CouncilAgent, message: str, context: Optional[list]) -> tuple:
    """Build system prompt and messages list for any LLM provider."""
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
    return conv_system, messages


def agent_ask_anthropic(agent: CouncilAgent, message: str, context: Optional[list], model_id: str, max_tokens: int = 300) -> tuple:
    """Call Anthropic Claude API. Returns (text, input_tokens, output_tokens)."""
    conv_system, messages = _build_conversation(agent, message, context)

    response = client.messages.create(
        model=model_id,
        max_tokens=max_tokens,
        system=conv_system,
        messages=messages,
    )
    return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens


def agent_ask_groq(agent: CouncilAgent, message: str, context: Optional[list], model_id: str, max_tokens: int = 300) -> tuple:
    """Call Groq API (OpenAI-compatible). Returns (text, input_tokens, output_tokens)."""
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY not configured — add it to .env (free at console.groq.com)")

    conv_system, messages = _build_conversation(agent, message, context)

    # Groq uses OpenAI-compatible format: system message + conversation
    groq_messages = [{"role": "system", "content": conv_system}] + messages

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": groq_messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }

    # Para resúmenes largos el timeout 30s puede no bastar
    timeout = 120 if max_tokens > 1000 else 30
    resp = http_requests.post(GROQ_API_URL, json=payload, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise ValueError(f"Groq API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def agent_ask(agent: CouncilAgent, message: str, context: Optional[list], llm_key: str = "claude-sonnet", max_tokens: int = 300) -> tuple:
    """Route to the correct LLM provider. Returns (text, input_tokens, output_tokens)."""
    model_cfg = LLM_MODELS.get(llm_key, LLM_MODELS["claude-sonnet"])

    if model_cfg["provider"] == "anthropic":
        return agent_ask_anthropic(agent, message, context, model_cfg["model_id"], max_tokens)
    elif model_cfg["provider"] == "groq":
        return agent_ask_groq(agent, message, context, model_cfg["model_id"], max_tokens)
    else:
        raise ValueError(f"Unknown provider: {model_cfg['provider']}")


def _send_query_report(question: str, replies: list, cost_eur: float, gen: str, llm_key: str = "claude-sonnet"):
    """Send a usage report with full responses to Telegram for historical record."""
    model_cfg = LLM_MODELS.get(llm_key, LLM_MODELS["claude-sonnet"])
    model_label = f"{model_cfg['icon']} {model_cfg['name']}"
    cost_label = "FREE" if model_cfg["free"] else f"€{cost_eur:.4f}"

    responses_txt = "\n\n".join(
        f"💬 *{r.icon} {r.name} ({r.persona})*:\n{r.content}"
        for r in replies
    )
    budget = _load_budget()
    msg = (
        f"📋 *Consejo AdmiraNext — Consulta*\n\n"
        f"🤖 Motor: {model_label}\n"
        f"❓ _{question[:300]}_\n\n"
        f"{responses_txt}\n\n"
        f"───────────────\n"
        f"💰 Coste: {cost_label} · "
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
    llm_key = req.llm if req.llm in LLM_MODELS else "claude-sonnet"
    model_cfg = LLM_MODELS[llm_key]

    # Only check budget for paid models
    if not model_cfg["free"]:
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
            None, agent_ask, agent, req.message, req.context, llm_key
        )
        track_usage(inp_tok, out_tok, agent.name, llm_key)
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
    _send_query_report(req.message, list(all_replies), query_cost, gen, llm_key)

    return AskResponse(racional=racional_replies, creativo=creativo_replies)


@app.post("/api/council/ask-one")
async def council_ask_one(
    req: AskOneRequest,
    _rate=Depends(check_rate_limit),
    _auth=Depends(verify_token),
):
    """Ask a single specific agent. Used by 'Preguntar' verb."""
    llm_key = req.llm if req.llm in LLM_MODELS else "claude-sonnet"
    model_cfg = LLM_MODELS[llm_key]

    # Only check budget for paid models
    if not model_cfg["free"]:
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
        None, agent_ask, agent, req.message, req.context, llm_key
    )
    track_usage(inp_tok, out_tok, agent.name, llm_key)

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
    _send_query_report(req.message, [reply], query_cost, gen, llm_key)

    return reply


@app.get("/api/council/models")
async def list_models():
    """List available LLM models."""
    models = []
    for key, cfg in LLM_MODELS.items():
        available = True
        if cfg["provider"] == "groq" and not GROQ_API_KEY:
            available = False
        models.append({
            "key": key,
            "name": cfg["name"],
            "icon": cfg["icon"],
            "free": cfg["free"],
            "provider": cfg["provider"],
            "available": available,
        })
    return {"models": models}


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


# ╔══════════════════════════════════════════════════════════════╗
# ║  PRESENTATIONS — Vídeos en la pantalla del Apple II          ║
# ╚══════════════════════════════════════════════════════════════╝

PRESENTATIONS_DIR = Path.home() / "Presentations" / "council"
PRESENTATIONS_STATE_FILE = PRESENTATIONS_DIR / "state.json"
PRESENTATIONS_DIR.mkdir(parents=True, exist_ok=True)


def _presentations_load_state() -> dict:
    if PRESENTATIONS_STATE_FILE.exists():
        try:
            return json.loads(PRESENTATIONS_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    return {"active": None, "items": []}


@app.get("/api/council/presentation")
async def council_presentation():
    """Devuelve la presentación activa (la que se muestra en el Apple II)."""
    state = _presentations_load_state()
    active_slug = state.get("active")
    if not active_slug:
        return None
    for item in state.get("items", []):
        if item.get("slug") == active_slug:
            return item
    return None


# Sirve los vídeos y posters. Externa via Funnel: /api/presentations/X.mp4
app.mount("/presentations", StaticFiles(directory=str(PRESENTATIONS_DIR)), name="presentations")


# ╔══════════════════════════════════════════════════════════════╗
# ║  DAILY BOOK — Un consejero pone un libro sobre la mesa       ║
# ╚══════════════════════════════════════════════════════════════╝

DAILY_STATE_DIR = Path.home() / ".council-daily"
DAILY_STATE_FILE = DAILY_STATE_DIR / "state.json"
AUDIO_DIR = Path.home() / "Audio" / "council-daily"
DAILY_STATE_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Voces macOS say por consejero
VOICE_MAP = {
    "CEO": "Eddy",       # Steve Jobs / Tim Cook (coetáneo)
    "CTO": "Grandpa",    # Wozniak
    "COO": "Eddy",
    "CFO": "Grandpa",    # Buffett
    "CCO": "Mónica",
    "CDO": "Paulina",
    "CXO": "Flo",
    "CSO": "Mónica",
}
DEFAULT_VOICE = "Eddy"

# Rotación de 16 días: leyendas primero, después coetáneos
DAILY_ROTATION = [
    ("leyendas", "CEO"), ("leyendas", "CTO"), ("leyendas", "COO"), ("leyendas", "CFO"),
    ("leyendas", "CCO"), ("leyendas", "CDO"), ("leyendas", "CXO"), ("leyendas", "CSO"),
    ("coetaneos", "CEO"), ("coetaneos", "CTO"), ("coetaneos", "COO"), ("coetaneos", "CFO"),
    ("coetaneos", "CCO"), ("coetaneos", "CDO"), ("coetaneos", "CXO"), ("coetaneos", "CSO"),
]


def _daily_load_state() -> dict:
    if DAILY_STATE_FILE.exists():
        try:
            return json.loads(DAILY_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    return {"rotation_index": 0, "history": []}


def _daily_save_state(state: dict):
    DAILY_STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _daily_find_today(state: dict) -> Optional[dict]:
    today = datetime.now().date().isoformat()
    for h in state["history"]:
        if h.get("date") == today:
            return h
    return None


def _daily_find_agent(generation: str, agent_name: str) -> CouncilAgent:
    group = AGENTS.get(generation, AGENTS["leyendas"])
    for cls in list(group["racional"]) + list(group["creativo"]):
        agent = get_agent(cls)
        if agent.name == agent_name:
            return agent
    raise ValueError(f"Agente no encontrado: {generation}/{agent_name}")


def _daily_history_titles(state: dict) -> list:
    return [h.get("title", "") for h in state["history"] if h.get("title")]


def _daily_pick_book(agent: CouncilAgent, llm_key: str, history_titles: list) -> dict:
    """Pide al agente que elija un libro nuevo. Devuelve {title, author, why}."""
    avoid = "\n".join(f"- {t}" for t in history_titles[-50:]) if history_titles else "(ninguno todavía)"
    prompt = (
        "Elige UN libro real y conocido relacionado con tu especialidad, filosofía y trayectoria, "
        "que NO esté en esta lista de libros ya tratados:\n"
        f"{avoid}\n\n"
        "Responde SOLO en formato JSON estricto, sin markdown, sin ``` y sin texto extra. "
        "Formato exacto:\n"
        '{"title": "Título exacto del libro", "author": "Nombre del autor", '
        '"why": "1-2 frases sobre por qué hoy lo pones sobre la mesa"}'
    )
    text, _, _ = agent_ask(agent, prompt, None, llm_key, max_tokens=400)
    t = text.strip()
    # Quita fences de markdown si los hay
    if t.startswith("```"):
        lines = t.split("\n")
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        t = "\n".join(lines)
    s, e = t.find("{"), t.rfind("}")
    if s < 0 or e <= s:
        raise ValueError(f"No se encontró JSON en la respuesta: {text[:300]}")
    data = json.loads(t[s:e + 1])
    return {
        "title": str(data.get("title", "")).strip(),
        "author": str(data.get("author", "")).strip(),
        "why": str(data.get("why", "")).strip(),
    }


def _daily_summary(agent: CouncilAgent, llm_key: str, title: str, author: str) -> str:
    prompt = (
        f"Cuéntame el libro '{title}' de {author} en tu propia voz, como si me lo "
        f"estuvieras explicando en una sobremesa. Habla en primera persona, conecta las "
        f"ideas del libro con tu experiencia y tu filosofía, da ejemplos concretos cuando "
        f"sirvan.\n\n"
        f"Importante: prosa fluida, sin títulos, sin listas, sin numeración, sin emojis. "
        f"Aproximadamente 1500 palabras (unos 8 minutos en voz alta). Empieza directamente "
        f"con la idea central del libro, sin saludos ni introducciones del estilo "
        f"'hola, hoy te voy a contar...'."
    )
    text, _, _ = agent_ask(agent, prompt, None, llm_key, max_tokens=3000)
    return text.strip()


def _daily_fetch_cover(title: str, author: str) -> Optional[str]:
    """Busca la portada en Google Books API (sin auth, gratis, legal)."""
    if not http_requests:
        return None
    try:
        for q_str in (f'intitle:"{title}" inauthor:"{author}"', f"{title} {author}"):
            q = urllib.parse.quote(q_str)
            url = f"https://www.googleapis.com/books/v1/volumes?q={q}&maxResults=1"
            r = http_requests.get(url, timeout=10)
            if r.status_code != 200:
                continue
            items = r.json().get("items", [])
            if not items:
                continue
            links = items[0].get("volumeInfo", {}).get("imageLinks", {})
            cover = links.get("thumbnail") or links.get("smallThumbnail")
            if cover:
                return cover.replace("http://", "https://")
    except Exception:
        pass
    return None


def _daily_voice_for(agent_name: str) -> str:
    return VOICE_MAP.get(agent_name, DEFAULT_VOICE)


def _daily_generate_audio(text: str, voice: str, out_path: Path):
    """Genera m4a/AAC con macOS `say`. Pasa el texto por stdin para evitar límites de argv."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "/usr/bin/say",
        "-v", voice,
        "--file-format=m4af",
        "--data-format=aac",
        "-o", str(out_path),
    ]
    res = subprocess.run(cmd, input=text, text=True, capture_output=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"say falló (code {res.returncode}): {res.stderr.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("say no produjo audio")


def daily_generate(llm_key: str = "llama-70b", force: bool = False) -> dict:
    """Genera (o devuelve si ya existe) el libro de hoy."""
    state = _daily_load_state()

    if not force:
        existing = _daily_find_today(state)
        if existing:
            return existing

    idx = state["rotation_index"] % len(DAILY_ROTATION)
    generation, agent_name = DAILY_ROTATION[idx]
    agent = _daily_find_agent(generation, agent_name)

    history_titles = _daily_history_titles(state)
    pick = _daily_pick_book(agent, llm_key, history_titles)
    if not pick.get("title"):
        raise RuntimeError("El consejero no devolvió título")

    cover = _daily_fetch_cover(pick["title"], pick["author"])
    summary = _daily_summary(agent, llm_key, pick["title"], pick["author"])

    today = datetime.now().date().isoformat()
    voice = _daily_voice_for(agent.name)
    # ASCII-safe filename para evitar problemas de URL-encoding y filesystems
    ascii_title = unicodedata.normalize("NFKD", pick["title"]).encode("ascii", "ignore").decode("ascii")
    safe_title = "".join(c if c.isalnum() else "_" for c in ascii_title)[:40].strip("_") or "book"
    audio_path = AUDIO_DIR / f"{today}_{agent.name}_{safe_title}.m4a"
    _daily_generate_audio(summary, voice, audio_path)

    entry = {
        "date": today,
        "generation": generation,
        "agent_name": agent.name,
        "agent_role": agent.role,
        "agent_persona": agent.persona,
        "agent_icon": ICONS.get(agent.name, "🎯"),
        "agent_side": getattr(agent, "side", "racional"),
        "title": pick["title"],
        "author": pick["author"],
        "why": pick["why"],
        "cover_url": cover,
        "summary_text": summary,
        "audio_filename": audio_path.name,
        "audio_url": f"/audio/{audio_path.name}",
        "voice": voice,
        "llm": llm_key,
        "created_at": datetime.now().isoformat(),
    }

    state["history"].append(entry)
    state["rotation_index"] = (state["rotation_index"] + 1) % len(DAILY_ROTATION)
    _daily_save_state(state)

    # Telegram opcional (no rompe si falla)
    try:
        msg = (
            f"📖 *Libro del día*\n\n"
            f"{entry['agent_icon']} *{agent.persona}* ({agent.role}) pone sobre la mesa:\n\n"
            f"📚 *{pick['title']}*\n"
            f"✍️ {pick['author']}\n\n"
            f"_{pick['why']}_"
        )
        _send_telegram(msg)
    except Exception as e:
        print(f"⚠️  Telegram notify failed: {e}")

    return entry


@app.get("/api/council/daily")
async def council_daily_get():
    """Devuelve el libro de hoy si existe (sin generar). null si todavía no hay."""
    state = _daily_load_state()
    return _daily_find_today(state)


class LeerRequest(BaseModel):
    llm: str = "llama-70b"
    force: bool = False


@app.post("/api/council/leer")
async def council_leer(
    req: LeerRequest,
    _rate=Depends(check_rate_limit),
    _auth=Depends(verify_token),
):
    """Trigger manual del libro del día. Si ya existe el de hoy, lo devuelve;
    en caso contrario genera uno nuevo (rotación + LLM + cover + audio)."""
    llm_key = req.llm if req.llm in LLM_MODELS else "llama-70b"
    if not LLM_MODELS[llm_key]["free"]:
        check_budget()
    loop = asyncio.get_event_loop()
    try:
        entry = await loop.run_in_executor(None, daily_generate, llm_key, req.force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"daily_generate failed: {e}")
    return entry


# Sirve los MP4/M4A generados. Externa via Funnel: /api/audio/X.m4a → backend /audio/X.m4a
app.mount("/audio", StaticFiles(directory=str(AUDIO_DIR)), name="audio")


# ── Run ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    budget = _load_budget()
    print("🏛️  AdmiraNext Council API v4.0 — http://localhost:8420")
    print(f"🔐 Token required: {bool(COUNCIL_API_TOKEN)}")
    print(f"🌐 Allowed origins: {ALLOWED_ORIGINS}")
    print(f"⏱️  Rate limit: {RATE_LIMIT_MAX} req / {RATE_LIMIT_WINDOW}s")
    print(f"💰 Budget: €{budget['total_cost_eur']:.4f} / €{BUDGET_LIMIT_EUR:.2f} "
          f"({budget['total_cost_eur']/BUDGET_LIMIT_EUR*100:.1f}% used)")
    print(f"📱 Telegram alerts: {'✅' if TELEGRAM_BOT_TOKEN else '❌'}")
    print(f"📧 Email alerts: {'✅' if SMTP_USER else '⚠️ Set SMTP_USER/SMTP_PASS in .env'}")
    print(f"🤖 LLM Models: Claude {'✅' if os.environ.get('ANTHROPIC_API_KEY') else '❌'} | "
          f"Groq (Llama/DeepSeek/Gemma) {'✅' if GROQ_API_KEY else '⚠️ Set GROQ_API_KEY in .env (free at console.groq.com)'}")
    uvicorn.run(app, host="0.0.0.0", port=8420)
