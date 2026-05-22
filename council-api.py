"""
Council API Bridge — Conecta el frontend SCUMM con los agentes del Consejo AdmiraNext.
Usa FastAPI + Anthropic SDK + Groq / Gemini / NVIDIA NIM para que cada consejero responda.

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
  - NVIDIA NIM (DeepSeek / GLM / MiniMax / GPT OSS) — trial / endpoint NVIDIA
"""

import sys
import os
import json
import asyncio
import time
import uuid
import smtplib
import random
import threading
import shutil
import tempfile
import csv
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

# Add admiranext to path — try multiple locations (optional, not needed on Render)
for _p in [
    os.path.expanduser("~/GitHub/admiranext"),
    os.path.expanduser("~/Documents/New project/csilvasantin-repos/admiranext"),
    os.environ.get("ADMIRANEXT_PATH", ""),
]:
    if _p and os.path.isdir(_p):
        sys.path.insert(0, _p)
        break

try:
    from admiranext.agents.base import CouncilAgent
    from admiranext.agents.racional.leyendas import CEO, CTO, COO, CFO
    from admiranext.agents.racional.coetaneos import (
        CEO_Coetaneo, CTO_Coetaneo, COO_Coetaneo, CFO_Coetaneo,
    )
    from admiranext.agents.creativo.leyendas import CCO, CDO, CXO, CSO
    from admiranext.agents.creativo.coetaneos import (
        CCO_Coetaneo, CDO_Coetaneo, CXO_Coetaneo, CSO_Coetaneo,
    )
    _ADMIRANEXT_AVAILABLE = True
except ImportError:
    _ADMIRANEXT_AVAILABLE = False
    # Stub mínimo para que el resto del código no explote en Render
    class CouncilAgent:
        name = "Unknown"
        def __init__(self, *a, **kw): pass
    CEO = CTO = COO = CFO = CouncilAgent
    CEO_Coetaneo = CTO_Coetaneo = COO_Coetaneo = CFO_Coetaneo = CouncilAgent
    CCO = CDO = CXO = CSO = CouncilAgent
    CCO_Coetaneo = CDO_Coetaneo = CXO_Coetaneo = CSO_Coetaneo = CouncilAgent

import re
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
    "gemini-flash": {
        "name": "Gemini 2.5 Flash",
        "provider": "gemini",
        "model_id": "gemini-2.5-flash",
        "free": False,
        "icon": "✨",
    },
    "nvidia-deepseek-v4-flash": {
        "name": "NVIDIA DeepSeek V4 Flash",
        "provider": "nvidia",
        "model_id": "deepseek-ai/deepseek-v4-flash",
        "free": True,
        "icon": "⚡",
    },
    "nvidia-glm47": {
        "name": "NVIDIA GLM 4.7",
        "provider": "nvidia",
        "model_id": "z-ai/glm4.7",
        "free": True,
        "icon": "🧠",
    },
    "nvidia-minimax-m27": {
        "name": "NVIDIA MiniMax M2.7",
        "provider": "nvidia",
        "model_id": "minimaxai/minimax-m2.7",
        "free": True,
        "icon": "🛠️",
    },
    "nvidia-gpt-oss-20b": {
        "name": "NVIDIA GPT OSS 20B",
        "provider": "nvidia",
        "model_id": "openai/gpt-oss-20b",
        "free": True,
        "icon": "🧪",
    },
}

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

YOUTUBE_RE = re.compile(r'https?://(?:www\.)?(?:youtube\.com/watch\?[^\s]*v=|youtu\.be/)[\w-]+')
HTTP_URL_RE = re.compile(r"^https?://", re.I)

# ── Config ──────────────────────────────────────────────────
COUNCIL_API_TOKEN = os.environ.get("COUNCIL_API_TOKEN", "")
ALLOWED_ORIGINS = [
    "https://csilvasantin.github.io",
    "https://www.admira.live",
    "https://admira.live",
    "http://localhost:8080",
    "http://localhost:3000",
    "http://localhost:3030",
    "http://localhost:3033",
    "http://127.0.0.1:8080",
    "http://127.0.0.1:3030",
    "http://127.0.0.1:3033",
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
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "-1003841065210")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "csilvasantin@gmail.com")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")

# Budget tracking file
BUDGET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "budget.json")
ENTRENAR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "entrenar_corpus.json")
ENTRENAR_FILE_BAK = ENTRENAR_FILE + ".bak"
YAR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yar_context.json")
YAR_FILE_BAK = YAR_FILE + ".bak"

# ── Budget tracker ───────────────────────────────────────────
_budget_lock = threading.Lock()
_alert_sent = {"warn": False, "critical": False, "blocked": False}
_entrenar_lock = threading.Lock()
_yar_lock = threading.Lock()


def _normalize_entrenar_item(raw) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url", "")).strip()
    if not url:
        return None
    source = str(raw.get("source", "Enlace")).strip() or "Enlace"
    kind = str(raw.get("kind", "other")).strip() or "other"
    title = str(raw.get("title", "")).strip()
    try:
        ts = int(raw.get("ts") or int(time.time() * 1000))
    except (TypeError, ValueError):
        ts = int(time.time() * 1000)
    item = {
        "url": url,
        "source": source[:80],
        "kind": kind[:40],
        "ts": ts,
    }
    if title:
        item["title"] = title[:300]
    return item


def _load_entrenar_store() -> dict:
    for candidate in [ENTRENAR_FILE, ENTRENAR_FILE_BAK]:
        if Path(candidate).exists():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except (json.JSONDecodeError, IOError):
                pass
    return {}


def _save_entrenar_store(data: dict):
    tmp_file = ENTRENAR_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    try:
        if Path(ENTRENAR_FILE).exists():
            Path(ENTRENAR_FILE).replace(ENTRENAR_FILE_BAK)
    except OSError:
        pass
    Path(tmp_file).replace(ENTRENAR_FILE)


def _merge_entrenar_items(existing: list, incoming: list) -> list:
    merged: dict[str, dict] = {}
    for item in (existing or []):
        norm = _normalize_entrenar_item(item)
        if norm:
            merged[norm["url"]] = norm
    for item in (incoming or []):
        norm = _normalize_entrenar_item(item)
        if not norm:
            continue
        prev = merged.get(norm["url"])
        if not prev or norm.get("ts", 0) >= prev.get("ts", 0):
            merged[norm["url"]] = norm
    return sorted(merged.values(), key=lambda x: (x.get("ts", 0), x.get("url", "")))


def _entrenar_gen_snapshot(gen: str) -> dict:
    store = _load_entrenar_store()
    raw_gen = store.get(gen, {})
    personas = {}
    total = 0
    if isinstance(raw_gen, dict):
        for persona, items in raw_gen.items():
            merged = _merge_entrenar_items([], items if isinstance(items, list) else [])
            personas[persona] = merged
            total += len(merged)
    return {"gen": gen, "personas": personas, "total": total}


_YAR_STATUS_RE = re.compile(r"^\s*(En proceso|Pendiente|Finalizada|Finalizado)\s*(?:[-:–—]\s*)?(.*)$", re.I)


def _clean_yar_line(item, limit: int = 240) -> str:
    txt = re.sub(r"\s+", " ", str(item or "")).strip()
    return txt[:limit]


def _yar_status_key(line: str, fallback: str = "pending") -> str:
    match = _YAR_STATUS_RE.match(str(line or ""))
    if not match:
        return fallback
    status = match.group(1).lower()
    if "proceso" in status:
        return "inProgress"
    if "finalizad" in status:
        return "done"
    return "pending"


def _normalize_yar_task_buckets(raw: dict, tasks: list[str], pending: list[str], done: list[str]) -> dict:
    raw_buckets = raw.get("taskBuckets") if isinstance(raw.get("taskBuckets"), dict) else {}

    def _list(key: str) -> list[str]:
        values = raw_buckets.get(key) if isinstance(raw_buckets.get(key), list) else []
        return [line for line in (_clean_yar_line(item) for item in values) if line][:12]

    in_progress = _list("inProgress")
    bucket_pending = _list("pending")
    bucket_done = _list("done")

    if not (in_progress or bucket_pending or bucket_done):
        active_source = tasks or pending
        derived_done = []
        for item in active_source:
            status = _yar_status_key(item, "pending")
            if status == "inProgress":
                in_progress.append(item)
            elif status == "done":
                derived_done.append(item)
            else:
                bucket_pending.append(item)
        bucket_done = done or derived_done
    elif done and not bucket_done:
        bucket_done = done

    return {
        "inProgress": in_progress[:12],
        "pending": bucket_pending[:12],
        "done": bucket_done[:12],
    }


def _normalize_yar_context(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    done = raw.get("done") if isinstance(raw.get("done"), list) else []
    cleaned_done = []
    for item in done:
        txt = _clean_yar_line(item)
        if txt:
            cleaned_done.append(txt)
    tasks = raw.get("tasks") if isinstance(raw.get("tasks"), list) else (raw.get("tareas") if isinstance(raw.get("tareas"), list) else [])
    cleaned_tasks = []
    for item in tasks:
        txt = _clean_yar_line(item)
        if txt:
            cleaned_tasks.append(txt)
    pending = raw.get("pending") if isinstance(raw.get("pending"), list) else []
    cleaned_pending = []
    for item in pending:
        txt = _clean_yar_line(item)
        if txt:
            cleaned_pending.append(txt)
    task_buckets = _normalize_yar_task_buckets(raw, cleaned_tasks, cleaned_pending, cleaned_done)
    cleaned_tasks = (task_buckets["inProgress"] + task_buckets["pending"])[:12] or cleaned_tasks[:12]
    cleaned_pending = cleaned_tasks[:12]
    cleaned_done = task_buckets["done"][:12] or cleaned_done[:12]
    active_task = _clean_yar_line(raw.get("activeTask") or (task_buckets["inProgress"][0] if task_buckets["inProgress"] else ""))
    day_start_at = str(raw.get("dayStartAt", "") or "").strip()
    day_end_at = str(raw.get("dayEndAt", "") or "").strip()
    sync_user = str(raw.get("syncUser", "") or "").strip()[:200]
    sync_source = str(raw.get("syncSource", "") or "").strip()[:80]
    return {
        "focus": str(raw.get("focus", "") or "").strip()[:240],
        "doing": str(raw.get("doing", "") or "").strip()[:600],
        "done": cleaned_done[:12],
        "tasks": cleaned_tasks[:12],
        "pending": cleaned_pending[:12],
        "taskBuckets": task_buckets,
        "activeTask": active_task,
        "ask": str(raw.get("ask", "") or "").strip()[:400],
        "updatedAt": str(raw.get("updatedAt", "") or "").strip() or datetime.now().isoformat(),
        "dayStartAt": day_start_at,
        "dayEndAt": day_end_at,
        "syncUser": sync_user,
        "syncSource": sync_source,
    }


def _merge_yar_day_meta(current: dict, next_data: dict) -> dict:
    now = datetime.now()
    current_start = str(current.get("dayStartAt", "") or "").strip()
    next_tasks = [str(x).strip() for x in (next_data.get("tasks") or []) if str(x).strip()]
    next_buckets = next_data.get("taskBuckets") if isinstance(next_data.get("taskBuckets"), dict) else {}
    has_in_progress = bool(next_buckets.get("inProgress")) or any(re.match(r"^En proceso\b", item, re.I) for item in next_tasks)
    same_day = False
    if current_start:
        try:
            same_day = datetime.fromisoformat(current_start.replace("Z", "+00:00")).date() == now.date()
        except Exception:
            same_day = False
    next_data["dayStartAt"] = current_start if (current_start and same_day) else now.isoformat()
    if has_in_progress:
        next_data["dayEndAt"] = ""
    else:
        current_end = str(current.get("dayEndAt", "") or "").strip()
        next_data["dayEndAt"] = current_end if (current_end and same_day) else now.isoformat()
    return next_data


def _load_yar_context() -> dict:
    for candidate in [YAR_FILE, YAR_FILE_BAK]:
        if Path(candidate).exists():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    return _normalize_yar_context(json.load(f))
            except (json.JSONDecodeError, IOError):
                pass
    return _normalize_yar_context({})


def _save_yar_context(data: dict):
    tmp_file = YAR_FILE + ".tmp"
    normalized = _normalize_yar_context(data)
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)
    try:
        if Path(YAR_FILE).exists():
            Path(YAR_FILE).replace(YAR_FILE_BAK)
    except OSError:
        pass
    Path(tmp_file).replace(YAR_FILE)


def _yar_log_dir() -> Path:
    log_dir = Path.home() / "Library" / "Logs" / "council-api"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _yar_tool_env() -> dict:
    env = os.environ.copy()
    env.setdefault("COUNCIL_API_BASE_URL", "http://127.0.0.1:8420")
    env.setdefault("COUNCIL_API_TOKEN", COUNCIL_API_TOKEN or "admira2026")
    return env


def _is_yarig_worker_process(pid: int) -> bool:
    try:
        res = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        cmdline = (res.stdout or "").strip()
        return (
            res.returncode == 0
            and "yarig-tasks-sync.mjs" in cmdline
            and ("--prepare-login" in cmdline or "--watch-after-login" in cmdline)
        )
    except Exception:
        return False


def _yar_iso_age_seconds(iso_text: str) -> Optional[int]:
    try:
        saved_dt = datetime.fromisoformat(str(iso_text or "").replace("Z", "+00:00"))
        now_dt = datetime.now(saved_dt.tzinfo) if saved_dt.tzinfo else datetime.now()
        return max(0, int((now_dt - saved_dt).total_seconds()))
    except Exception:
        return None


def _yar_worker_status() -> dict:
    log_dir = _yar_log_dir()
    pid_file = log_dir / "yarig-login.pid"
    snapshot_path = log_dir / "yarig-last.json"
    login_log_path = log_dir / "yarig-login.log"
    pid = None
    watcher_alive = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            watcher_alive = _is_yarig_worker_process(pid)
        except Exception:
            pid = None
    snapshot = {}
    if snapshot_path.exists():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            snapshot = {}
    saved_at = str(snapshot.get("savedAt", "") or "").strip()
    age_seconds = _yar_iso_age_seconds(saved_at)
    fresh = bool(age_seconds is not None and age_seconds <= int(os.environ.get("YARIG_STALE_SECONDS", "900")))
    return {
        "watcherAlive": watcher_alive,
        "pid": pid,
        "pidFile": str(pid_file),
        "snapshotPath": str(snapshot_path),
        "loginLogPath": str(login_log_path),
        "snapshotSavedAt": saved_at,
        "snapshotAgeSeconds": age_seconds,
        "snapshotFresh": fresh,
        "snapshotSource": str(snapshot.get("source", "") or "").strip(),
        "loginUser": str(snapshot.get("loginUser", "") or "").strip(),
    }


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

@app.get("/")
async def root():
    return {"status": "ok", "service": "AdmiraNext Council API", "version": "Admira v.26.05.07.r6"}

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
    confirm_expensive_video: bool = False


class AskOneRequest(BaseModel):
    message: str
    agent_name: str  # e.g. "CEO", "CTO", "CCO"...
    generation: str = "leyendas"
    context: Optional[list] = None
    llm: str = "claude-sonnet"  # LLM model key from LLM_MODELS
    confirm_expensive_video: bool = False


class AnalyzeYoutubeRequest(BaseModel):
    url: str
    question: Optional[str] = None
    note: Optional[str] = None


class ImportVideoRequest(BaseModel):
    url: str
    subdir: str = "AdmiraNext/Importados"


class YarContextRequest(BaseModel):
    focus: str = ""
    doing: str = ""
    done: list[str] = []
    tasks: list[str] = []
    pending: list[str] = []
    taskBuckets: dict = {}
    activeTask: str = ""
    ask: str = ""
    syncUser: str = ""
    syncSource: str = ""


class YarTaskActionRequest(BaseModel):
    action: str = ""
    taskHint: str = ""


class YarTaskCreateRequest(BaseModel):
    description: str = ""
    estimateHours: int = 1
    project: str = "Admira"


class YarProjectsRequest(BaseModel):
    refresh: bool = False


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


def _requires_expensive_video_confirmation(llm_key: str, message: str) -> bool:
    """Flag Gemini + YouTube requests because they can consume a very large token budget."""
    model_cfg = LLM_MODELS.get(llm_key, LLM_MODELS["claude-sonnet"])
    return model_cfg["provider"] == "gemini" and bool(YOUTUBE_RE.search(message or ""))


def _is_expensive_video_request(llm_key: str, message: str) -> bool:
    """Gemini + YouTube should be limited to a single agent even after confirmation."""
    return _requires_expensive_video_confirmation(llm_key, message)


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


def agent_ask_nvidia(agent: CouncilAgent, message: str, context: Optional[list], model_id: str, max_tokens: int = 300) -> tuple:
    """Call NVIDIA NIM API (OpenAI-compatible chat completions)."""
    if not NVIDIA_API_KEY:
        raise ValueError("NVIDIA_API_KEY not configured — añade la clave de build.nvidia.com / NIM al .env")

    conv_system, messages = _build_conversation(agent, message, context)
    nvidia_messages = [{"role": "system", "content": conv_system}] + messages

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": nvidia_messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }

    timeout = 120 if max_tokens > 1000 else 45
    resp = http_requests.post(NVIDIA_API_URL, json=payload, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise ValueError(f"NVIDIA API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def agent_ask_gemini(agent: CouncilAgent, message: str, context: Optional[list], model_id: str, max_tokens: int = 300) -> tuple:
    """Call Google Gemini API. Supports YouTube URLs as native video input."""
    try:
        import google.generativeai as genai
    except ImportError:
        raise ValueError("google-generativeai not installed — pip install google-generativeai")

    if not GOOGLE_API_KEY:
        raise ValueError("GOOGLE_API_KEY not set — añade GOOGLE_API_KEY al .env (obtén la clave en aistudio.google.com)")

    genai.configure(api_key=GOOGLE_API_KEY)
    conv_system, _ = _build_conversation(agent, message, context)

    model = genai.GenerativeModel(
        model_name=model_id,
        system_instruction=conv_system,
    )

    # Detect YouTube URL → pass as native video part
    yt_match = YOUTUBE_RE.search(message)
    if yt_match:
        yt_url = yt_match.group(0)
        text_part = (message[:yt_match.start()] + message[yt_match.end():]).strip()
        text_part = text_part or "Analiza este vídeo desde tu perspectiva y expertise:"
        parts = [
            text_part,
            genai.protos.Part(file_data=genai.protos.FileData(
                file_uri=yt_url,
                mime_type="video/youtube",
            )),
        ]
        # Video analysis needs more tokens: 300 corta respuestas a mitad.
        effective_max = max(max_tokens, 800)
    else:
        parts = [message]
        effective_max = max_tokens

    response = model.generate_content(
        parts,
        generation_config=genai.GenerationConfig(
            max_output_tokens=effective_max,
            temperature=0.7,
        ),
    )

    text = response.text
    usage = response.usage_metadata
    in_tok = getattr(usage, "prompt_token_count", 0) or 0
    out_tok = getattr(usage, "candidates_token_count", 0) or 0
    return text, in_tok, out_tok


def agent_ask(agent: CouncilAgent, message: str, context: Optional[list], llm_key: str = "claude-sonnet", max_tokens: int = 300) -> tuple:
    """Route to the correct LLM provider. Returns (text, input_tokens, output_tokens)."""
    model_cfg = LLM_MODELS.get(llm_key, LLM_MODELS["claude-sonnet"])

    if model_cfg["provider"] == "anthropic":
        return agent_ask_anthropic(agent, message, context, model_cfg["model_id"], max_tokens)
    elif model_cfg["provider"] == "groq":
        return agent_ask_groq(agent, message, context, model_cfg["model_id"], max_tokens)
    elif model_cfg["provider"] == "gemini":
        return agent_ask_gemini(agent, message, context, model_cfg["model_id"], max_tokens)
    elif model_cfg["provider"] == "nvidia":
        return agent_ask_nvidia(agent, message, context, model_cfg["model_id"], max_tokens)
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

    if _requires_expensive_video_confirmation(llm_key, req.message) and not req.confirm_expensive_video:
        raise HTTPException(
            status_code=409,
            detail="Gemini + YouTube requiere confirmación explícita por coste antes de enviar la consulta.",
        )

    # Only check budget for paid models
    if not model_cfg["free"]:
        check_budget()

    gen = req.generation if req.generation in AGENTS else "leyendas"
    group = AGENTS[gen]
    loop = asyncio.get_event_loop()
    expensive_video_request = _is_expensive_video_request(llm_key, req.message)

    if expensive_video_request:
        # Hard cap: one single agent for Gemini + YouTube requests.
        selected = [random.choice(list(group["racional"]) + list(group["creativo"]))]
    else:
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

    if _requires_expensive_video_confirmation(llm_key, req.message) and not req.confirm_expensive_video:
        raise HTTPException(
            status_code=409,
            detail="Gemini + YouTube requiere confirmación explícita por coste antes de enviar la consulta.",
        )

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
        if cfg["provider"] == "gemini" and not GOOGLE_API_KEY:
            available = False
        if cfg["provider"] == "nvidia" and not NVIDIA_API_KEY:
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


@app.get("/api/council/yar-context")
async def get_yar_context(_auth=Depends(verify_token)):
    with _yar_lock:
        return _load_yar_context()


@app.get("/api/council/yar-status")
async def get_yar_status(_auth=Depends(verify_token)):
    with _yar_lock:
        context = _load_yar_context()
    worker = _yar_worker_status()
    context_age = _yar_iso_age_seconds(context.get("updatedAt", ""))
    return {
        "ok": True,
        "contextUpdatedAt": context.get("updatedAt", ""),
        "contextAgeSeconds": context_age,
        "contextFresh": bool(context_age is not None and context_age <= int(os.environ.get("YARIG_STALE_SECONDS", "900"))),
        "counts": {
            "inProgress": len((context.get("taskBuckets") or {}).get("inProgress") or []),
            "pending": len((context.get("taskBuckets") or {}).get("pending") or []),
            "done": len((context.get("taskBuckets") or {}).get("done") or []),
        },
        "syncUser": context.get("syncUser", ""),
        "syncSource": context.get("syncSource", ""),
        "worker": worker,
    }


@app.post("/api/council/yar-context")
async def save_yar_context(req: YarContextRequest, _auth=Depends(verify_token)):
    with _yar_lock:
        current = _load_yar_context()
        data = {
            "focus": req.focus,
            "doing": req.doing,
            "done": req.done,
            "tasks": req.tasks or req.pending,
            "pending": req.tasks or req.pending,
            "taskBuckets": req.taskBuckets,
            "activeTask": req.activeTask,
            "ask": req.ask,
            "updatedAt": datetime.now().isoformat(),
            "syncUser": req.syncUser or current.get("syncUser", ""),
            "syncSource": req.syncSource or current.get("syncSource", ""),
        }
        data = _merge_yar_day_meta(current, data)
        _save_yar_context(data)
        return _load_yar_context()


@app.post("/api/council/yar-sync")
async def sync_yar_context_from_logged_session(_auth=Depends(verify_token)):
    tool_path = Path(__file__).resolve().parent / "tools" / "yarig-tasks-sync.mjs"
    snapshot_path = Path.home() / "Library" / "Logs" / "council-api" / "yarig-last.json"
    login_log_path = Path.home() / "Library" / "Logs" / "council-api" / "yarig-login.log"
    if not tool_path.exists():
        raise HTTPException(status_code=501, detail="yarig-tasks-sync.mjs no disponible en este backend")

    def _parse_yar_tasks_from_text(text: str) -> dict:
        chunks = re.split(r"Tarea añadida el \d{2}/\d{2}/\d{4}:", text or "")
        in_progress, pending, done = [], [], []
        for chunk in chunks[1:]:
            desc_match = re.search(r"Descripción:\s*([^\n]+)", chunk)
            status_match = re.search(r"\b(En proceso|Pendiente|Finalizada|Finalizado)\b", chunk)
            desc = desc_match.group(1).strip() if desc_match else ""
            status = status_match.group(1).strip() if status_match else "Pendiente"
            if not desc:
                continue
            line = f"{status} - {desc}"[:240]
            if re.fullmatch(r"Finalizad[ao]", status, re.I):
                done.append(line)
            elif re.fullmatch(r"En proceso", status, re.I):
                in_progress.append(line)
            else:
                pending.append(line)
        active = (in_progress + pending)[:12]
        return {
            "tasks": active[:12],
            "done": done[:12],
            "taskBuckets": {
                "inProgress": in_progress[:12],
                "pending": pending[:12],
                "done": done[:12],
            },
            "activeTask": in_progress[0] if in_progress else "",
        }

    def _run_osascript(lines: list[str], timeout: int = 6) -> str:
        args = []
        for line in lines:
            args.extend(["-e", line])
        res = subprocess.run(["osascript", *args], capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "osascript error").strip())
        return (res.stdout or "").strip()

    def _scrape_yar_from_browser_tabs() -> Optional[dict]:
        js = 'JSON.stringify({title:document.title || "", body:(document.body && document.body.innerText) ? document.body.innerText : ""})'
        scripts = [
            [
                'tell application "Safari"',
                'repeat with w in windows',
                'repeat with t in tabs of w',
                'try',
                'set tabUrl to URL of t',
                'if tabUrl contains "yarig.ai" then',
                'set payload to do JavaScript "' + js.replace('"', '\\"') + '" in t',
                'return tabUrl & linefeed & payload',
                'end if',
                'end try',
                'end repeat',
                'end repeat',
                'end tell',
                'return ""',
            ],
            [
                'tell application "Google Chrome"',
                'repeat with w in windows',
                'repeat with t in tabs of w',
                'try',
                'set tabUrl to URL of t',
                'if tabUrl contains "yarig.ai" then',
                'set payload to execute t javascript "' + js.replace('"', '\\"') + '"',
                'return tabUrl & linefeed & payload',
                'end if',
                'end try',
                'end repeat',
                'end repeat',
                'end tell',
                'return ""',
            ],
        ]
        for lines in scripts:
            try:
                raw = _run_osascript(lines)
                if not raw:
                    continue
                first_break = raw.find("\n")
                if first_break == -1:
                    continue
                source_url = raw[:first_break].strip()
                payload_text = raw[first_break + 1:].strip()
                payload = json.loads(payload_text)
                body = str(payload.get("body") or "").strip()
                if "Mis tareas" not in body:
                    continue
                parsed = _parse_yar_tasks_from_text(body)
                return {
                    "currentUrl": source_url,
                    "title": str(payload.get("title") or "").strip(),
                    "tasks": parsed["tasks"],
                    "done": parsed["done"],
                    "taskBuckets": parsed["taskBuckets"],
                    "activeTask": parsed["activeTask"],
                    "source": "browser-tab",
                    "loginUser": "",
                }
            except Exception:
                continue
        return None

    def _load_recent_snapshot(max_age_seconds: int = 12 * 3600) -> Optional[dict]:
        def _normalize_snapshot(raw: dict, source_name: str) -> Optional[dict]:
            saved_at = str(raw.get("savedAt", "") or "").strip()
            if saved_at:
                saved_dt = datetime.fromisoformat(saved_at.replace("Z", "+00:00"))
                now_dt = datetime.now(saved_dt.tzinfo) if saved_dt.tzinfo else datetime.now()
                if (now_dt - saved_dt).total_seconds() > max_age_seconds:
                    return None
            normalized = _normalize_yar_context(raw)
            tasks = normalized["tasks"]
            done = normalized["done"]
            if not tasks and not done:
                return None
            return {
                "currentUrl": str(raw.get("currentUrl", "") or "").strip(),
                "title": str(raw.get("title", "") or "").strip(),
                "tasks": tasks,
                "done": done,
                "taskBuckets": normalized["taskBuckets"],
                "activeTask": normalized["activeTask"],
                "source": str(raw.get("source", "") or source_name).strip() or source_name,
                "savedAt": saved_at,
                "loginUser": str(raw.get("loginUser", "") or raw.get("syncUser", "")).strip(),
            }

        try:
            if not snapshot_path.exists():
                raise FileNotFoundError
            raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
            normalized = _normalize_snapshot(raw, "snapshot")
            if normalized:
                return normalized
        except Exception:
            pass
        try:
            if not login_log_path.exists():
                return None
            log_text = login_log_path.read_text(encoding="utf-8", errors="ignore")
            matches = re.findall(r'(\{"ok":true,"prepared":true.*?\})', log_text, re.DOTALL)
            if not matches:
                return None
            raw = json.loads(matches[-1])
            raw["savedAt"] = raw.get("savedAt") or datetime.fromtimestamp(login_log_path.stat().st_mtime).isoformat()
            return _normalize_snapshot(raw, "login-log")
        except Exception:
            return None

    env = _yar_tool_env()
    payload = _scrape_yar_from_browser_tabs()
    if not payload:
        cmd = ["node", str(tool_path), "--dump-json"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=90, env=env)
        except subprocess.TimeoutExpired as e:
            payload = _load_recent_snapshot()
            if not payload:
                raise HTTPException(status_code=504, detail=f"yarig sync timeout: {(e.stderr or e.stdout or '').strip()[:240]}")
        except FileNotFoundError:
            raise HTTPException(status_code=501, detail="node no disponible para lanzar yarig sync")

        if not payload and res.returncode != 0:
            detail = (res.stderr or res.stdout or "yarig sync failed").strip()[:400]
            if "ProcessSingleton" in detail or "already in use by another instance of Chromium" in detail:
                payload = _load_recent_snapshot()
                if not payload:
                    raise HTTPException(
                        status_code=409,
                        detail="El perfil persistente de Yarig.AI está abierto en otra ventana. Termina el login, espera a que esa ventana se cierre y luego repite /yarig.ai sincro.",
                    )
            else:
                payload = _load_recent_snapshot()
                if not payload:
                    raise HTTPException(status_code=502, detail=detail)

        if not payload:
            try:
                payload = json.loads((res.stdout or "").strip() or "{}")
            except json.JSONDecodeError as e:
                payload = _load_recent_snapshot()
                if not payload:
                    raise HTTPException(status_code=502, detail=f"yarig sync devolvió JSON inválido: {e}")

    normalized_payload = _normalize_yar_context(payload)
    tasks = normalized_payload["tasks"]
    done = normalized_payload["done"]
    task_buckets = normalized_payload["taskBuckets"]
    active_task = normalized_payload["activeTask"]

    with _yar_lock:
        current = _load_yar_context()
        data = {
            "focus": current.get("focus", ""),
            "doing": current.get("doing", ""),
            "done": done,
            "tasks": tasks,
            "pending": tasks,
            "taskBuckets": task_buckets,
            "activeTask": active_task,
            "ask": current.get("ask", ""),
            "updatedAt": datetime.now().isoformat(),
            "syncUser": str(payload.get("loginUser", "") or payload.get("syncUser", "") or current.get("syncUser", "")).strip(),
            "syncSource": str(payload.get("source", "") or current.get("syncSource", "")).strip(),
        }
        data = _merge_yar_day_meta(current, data)
        _save_yar_context(data)
        context = _load_yar_context()
    return {
        "ok": True,
        "context": context,
        "sourceUrl": payload.get("currentUrl", ""),
        "sourceTitle": payload.get("title", ""),
        "source": payload.get("source", "worker"),
        "snapshotSavedAt": payload.get("savedAt", ""),
        "imported": {
            "tasks": len(tasks),
            "done": len(done),
        },
    }


@app.post("/api/council/yar-task-action")
async def yar_task_action(req: YarTaskActionRequest, _auth=Depends(verify_token)):
    tool_path = Path(__file__).resolve().parent / "tools" / "yarig-tasks-sync.mjs"
    if not tool_path.exists():
        raise HTTPException(status_code=501, detail="yarig-tasks-sync.mjs no disponible en este backend")

    action = str(req.action or "").strip().lower()
    if action not in {"pause", "cancel", "finalize"}:
        raise HTTPException(status_code=400, detail="Acción de Yarig no soportada")

    env = _yar_tool_env()
    log_dir = _yar_log_dir()
    login_log = log_dir / "yarig-login.log"
    pid_file = log_dir / "yarig-login.pid"

    had_watcher = False
    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text(encoding="utf-8").strip())
            if _is_yarig_worker_process(existing_pid):
                had_watcher = True
                try:
                    subprocess.run(["kill", "-9", str(existing_pid)], capture_output=True, text=True, timeout=5)
                except Exception:
                    pass
                time.sleep(1.0)
            pid_file.unlink(missing_ok=True)
        except Exception:
            try:
                pid_file.unlink(missing_ok=True)
            except Exception:
                pass

    task_hint = _clean_yar_line(req.taskHint)
    cmd = ["node", str(tool_path), "--task-action", action]
    if task_hint:
        cmd.extend(["--task-hint", task_hint])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail=f"yarig task action timeout: {(e.stderr or e.stdout or '').strip()[:240]}")
    except FileNotFoundError:
        raise HTTPException(status_code=501, detail="node no disponible para lanzar yarig task action")

    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "yarig task action failed").strip()[:500]
        if "ProcessSingleton" in detail or "already in use by another instance of Chromium" in detail:
            raise HTTPException(status_code=409, detail="El perfil persistente de Yarig.AI está ocupado por otra ventana o watcher. Cierra esa sesión del sync y vuelve a intentarlo.")
        if "login" in detail.lower() or "auth" in detail.lower():
            raise HTTPException(status_code=401, detail="La sesión persistente de Yarig.AI necesita login antes de controlar tareas.")
        raise HTTPException(status_code=502, detail=detail)

    try:
        payload = json.loads((res.stdout or "").strip() or "{}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"yarig task action devolvió JSON inválido: {e}")
    finally:
        if had_watcher:
            out = open(login_log, "a", encoding="utf-8")
            try:
                proc = subprocess.Popen(
                    ["node", str(tool_path), "--watch-after-login"],
                    stdout=out,
                    stderr=out,
                    text=True,
                    env=env,
                    cwd=str(Path(__file__).resolve().parent),
                    start_new_session=True,
                )
                pid_file.write_text(str(proc.pid), encoding="utf-8")
            except Exception:
                out.close()

    context = payload.get("context") or _load_yar_context()
    return {
        "ok": True,
        "action": action,
        "context": context,
        "currentTask": payload.get("currentTask", ""),
        "sourceUrl": payload.get("currentUrl", ""),
        "sourceTitle": payload.get("title", ""),
    }


@app.post("/api/council/yar-create-task")
async def yar_create_task(req: YarTaskCreateRequest, _auth=Depends(verify_token)):
    tool_path = Path(__file__).resolve().parent / "tools" / "yarig-tasks-sync.mjs"
    if not tool_path.exists():
        raise HTTPException(status_code=501, detail="yarig-tasks-sync.mjs no disponible en este backend")

    description = _clean_yar_line(req.description)
    if not description:
        raise HTTPException(status_code=400, detail="La descripción de la tarea es obligatoria")

    estimate_hours = int(req.estimateHours or 1)
    if estimate_hours < 1 or estimate_hours > 8:
        raise HTTPException(status_code=400, detail="estimateHours debe estar entre 1 y 8")

    project = _clean_yar_line(req.project or "Admira") or "Admira"

    env = _yar_tool_env()
    log_dir = _yar_log_dir()
    login_log = log_dir / "yarig-login.log"
    pid_file = log_dir / "yarig-login.pid"

    had_watcher = False
    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text(encoding="utf-8").strip())
            if _is_yarig_worker_process(existing_pid):
                had_watcher = True
                try:
                    subprocess.run(["kill", "-9", str(existing_pid)], capture_output=True, text=True, timeout=5)
                except Exception:
                    pass
                time.sleep(1.0)
            pid_file.unlink(missing_ok=True)
        except Exception:
            try:
                pid_file.unlink(missing_ok=True)
            except Exception:
                pass

    cmd = [
        "node",
        str(tool_path),
        "--create-task",
        "--task-desc",
        description,
        "--task-project",
        project,
        "--task-estimate",
        str(estimate_hours),
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail=f"yarig create task timeout: {(e.stderr or e.stdout or '').strip()[:240]}")
    except FileNotFoundError:
        raise HTTPException(status_code=501, detail="node no disponible para lanzar yarig create task")

    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "yarig create task failed").strip()[:500]
        if "ProcessSingleton" in detail or "already in use by another instance of Chromium" in detail:
            raise HTTPException(status_code=409, detail="El perfil persistente de Yarig.AI está ocupado por otra ventana o watcher. Cierra esa sesión del sync y vuelve a intentarlo.")
        if "login" in detail.lower() or "auth" in detail.lower() or "identif" in detail.lower():
            raise HTTPException(status_code=401, detail="La sesión persistente de Yarig.AI necesita login antes de crear tareas.")
        raise HTTPException(status_code=502, detail=detail)

    try:
        payload = json.loads((res.stdout or "").strip() or "{}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"yarig create task devolvió JSON inválido: {e}")
    finally:
        if had_watcher:
            out = open(login_log, "a", encoding="utf-8")
            try:
                proc = subprocess.Popen(
                    ["node", str(tool_path), "--watch-after-login"],
                    stdout=out,
                    stderr=out,
                    text=True,
                    env=env,
                    cwd=str(Path(__file__).resolve().parent),
                    start_new_session=True,
                )
                pid_file.write_text(str(proc.pid), encoding="utf-8")
            except Exception:
                out.close()

    context = payload.get("context") or _load_yar_context()
    return {
        "ok": True,
        "context": context,
        "createdTask": payload.get("createdTask", description),
        "estimateHours": estimate_hours,
        "project": payload.get("project", project),
        "sourceUrl": payload.get("currentUrl", ""),
        "sourceTitle": payload.get("title", ""),
    }


@app.post("/api/council/yar-projects")
async def yar_projects(req: YarProjectsRequest | None = None, _auth=Depends(verify_token)):
    tool_path = Path(__file__).resolve().parent / "tools" / "yarig-tasks-sync.mjs"
    if not tool_path.exists():
        raise HTTPException(status_code=501, detail="yarig-tasks-sync.mjs no disponible en este backend")

    env = _yar_tool_env()
    log_dir = _yar_log_dir()
    login_log = log_dir / "yarig-login.log"
    pid_file = log_dir / "yarig-login.pid"

    had_watcher = False
    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text(encoding="utf-8").strip())
            if _is_yarig_worker_process(existing_pid):
                had_watcher = True
                try:
                    subprocess.run(["kill", "-9", str(existing_pid)], capture_output=True, text=True, timeout=5)
                except Exception:
                    pass
                time.sleep(1.0)
            pid_file.unlink(missing_ok=True)
        except Exception:
            try:
                pid_file.unlink(missing_ok=True)
            except Exception:
                pass

    cmd = ["node", str(tool_path), "--list-projects"]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail=f"yarig list projects timeout: {(e.stderr or e.stdout or '').strip()[:240]}")
    except FileNotFoundError:
        raise HTTPException(status_code=501, detail="node no disponible para lanzar yarig list projects")

    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "yarig list projects failed").strip()[:500]
        if "ProcessSingleton" in detail or "already in use by another instance of Chromium" in detail:
            raise HTTPException(status_code=409, detail="El perfil persistente de Yarig.AI está ocupado por otra ventana o watcher. Cierra esa sesión del sync y vuelve a intentarlo.")
        if "login" in detail.lower() or "auth" in detail.lower() or "identif" in detail.lower():
            raise HTTPException(status_code=401, detail="La sesión persistente de Yarig.AI necesita login antes de listar proyectos.")
        raise HTTPException(status_code=502, detail=detail)

    try:
        payload = json.loads((res.stdout or "").strip() or "{}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"yarig list projects devolvió JSON inválido: {e}")
    finally:
        if had_watcher:
            out = open(login_log, "a", encoding="utf-8")
            try:
                proc = subprocess.Popen(
                    ["node", str(tool_path), "--watch-after-login"],
                    stdout=out,
                    stderr=out,
                    text=True,
                    env=env,
                    cwd=str(Path(__file__).resolve().parent),
                    start_new_session=True,
                )
                pid_file.write_text(str(proc.pid), encoding="utf-8")
            except Exception:
                out.close()

    projects = [
        _clean_yar_line(item, limit=160)
        for item in (payload.get("projects") or [])
        if _clean_yar_line(item, limit=160)
    ]
    deduped_projects = list(dict.fromkeys(projects))

    return {
        "ok": True,
        "projects": deduped_projects,
        "count": len(deduped_projects),
        "sourceUrl": payload.get("currentUrl", ""),
        "sourceTitle": payload.get("title", ""),
        "refreshedAt": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/api/council/yar-login")
async def prepare_yar_login_session(_auth=Depends(verify_token)):
    tool_path = Path(__file__).resolve().parent / "tools" / "yarig-tasks-sync.mjs"
    if not tool_path.exists():
        raise HTTPException(status_code=501, detail="yarig-tasks-sync.mjs no disponible en este backend")

    env = _yar_tool_env()
    log_dir = _yar_log_dir()
    login_log = log_dir / "yarig-login.log"
    pid_file = log_dir / "yarig-login.pid"

    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text(encoding="utf-8").strip())
            if _is_yarig_worker_process(existing_pid):
                return {
                    "ok": True,
                    "pid": existing_pid,
                    "message": "Ya hay una ventana persistente de login de Yarig.ai abierta",
                    "logPath": str(login_log),
                }
            pid_file.unlink()
        except Exception:
            try:
                pid_file.unlink()
            except Exception:
                pass
    out = open(login_log, "a", encoding="utf-8")
    cmd = ["node", str(tool_path), "--watch-after-login"]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=out,
            stderr=out,
            text=True,
            env=env,
            cwd=str(Path(__file__).resolve().parent),
            start_new_session=True,
        )
    except FileNotFoundError:
        out.close()
        raise HTTPException(status_code=501, detail="node no disponible para lanzar yarig login")
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    return {
        "ok": True,
        "pid": proc.pid,
        "message": "Ventana de Yarig.ai abierta para login y watcher persistente del sync",
        "logPath": str(login_log),
    }


@app.post("/api/council/yar-logout")
async def yar_logout_session(_auth=Depends(verify_token)):
    tool_path = Path(__file__).resolve().parent / "tools" / "yarig-tasks-sync.mjs"
    if not tool_path.exists():
        raise HTTPException(status_code=501, detail="yarig-tasks-sync.mjs no disponible en este backend")

    env = _yar_tool_env()
    log_dir = _yar_log_dir()
    pid_file = log_dir / "yarig-login.pid"

    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text(encoding="utf-8").strip())
            if _is_yarig_worker_process(existing_pid):
                try:
                    subprocess.run(["kill", "-9", str(existing_pid)], capture_output=True, text=True, timeout=5)
                except Exception:
                    pass
            pid_file.unlink(missing_ok=True)
        except Exception:
            try:
                pid_file.unlink(missing_ok=True)
            except Exception:
                pass

    cmd = ["node", str(tool_path), "--logout"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail=f"yarig logout timeout: {(e.stderr or e.stdout or '').strip()[:240]}")
    except FileNotFoundError:
        raise HTTPException(status_code=501, detail="node no disponible para lanzar yarig logout")

    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "yarig logout failed").strip()[:500]
        raise HTTPException(status_code=502, detail=detail)

    try:
        payload = json.loads((res.stdout or "").strip() or "{}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"yarig logout devolvió JSON inválido: {e}")

    with _yar_lock:
        current = _load_yar_context()
        data = {
            "focus": current.get("focus", ""),
            "doing": current.get("doing", ""),
            "done": [],
            "tasks": [],
            "pending": [],
            "taskBuckets": {"inProgress": [], "pending": [], "done": []},
            "activeTask": "",
            "ask": current.get("ask", ""),
            "updatedAt": datetime.now().isoformat(),
            "syncUser": "",
            "syncSource": "",
        }
        data = _merge_yar_day_meta(current, data)
        _save_yar_context(data)
        context = _load_yar_context()

    return {
        "ok": True,
        "logout": True,
        "context": context,
        "sourceUrl": payload.get("currentUrl", ""),
        "sourceTitle": payload.get("title", ""),
    }


def _yt_clean_text(text: str) -> str:
    text = (text or "").replace("\r", "\n")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _yt_pick_caption_track(info: dict) -> Optional[dict]:
    preferred_langs = ("es", "es-es", "es-419", "en", "en-us", "en-gb")
    groups = [
        info.get("subtitles") or {},
        info.get("automatic_captions") or {},
    ]
    for langs in groups:
        if not isinstance(langs, dict):
            continue
        lower_map = {str(k).lower(): v for k, v in langs.items()}
        for lang in preferred_langs:
            tracks = lower_map.get(lang)
            if not isinstance(tracks, list):
                continue
            for ext in ("json3", "srv3", "vtt", "ttml"):
                for track in tracks:
                    if str(track.get("ext", "")).lower() == ext and track.get("url"):
                        return track
            for track in tracks:
                if track.get("url"):
                    return track
    return None


def _yt_download_caption_text(track: dict) -> str:
    if not track or not track.get("url") or not http_requests:
        return ""
    resp = http_requests.get(track["url"], timeout=30)
    if resp.status_code != 200:
        return ""
    ext = str(track.get("ext", "")).lower()
    if ext in ("json3", "srv3"):
        try:
            data = resp.json()
            out = []
            for ev in data.get("events", []):
                segs = ev.get("segs") or []
                parts = [seg.get("utf8", "") for seg in segs if isinstance(seg, dict)]
                chunk = "".join(parts).strip()
                if chunk:
                    out.append(chunk)
            return _yt_clean_text("\n".join(out))
        except Exception:
            return ""
    text = resp.text
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "WEBVTT":
            continue
        if "-->" in stripped:
            continue
        if re.fullmatch(r"[0-9]+", stripped):
            continue
        lines.append(stripped)
    return _yt_clean_text("\n".join(lines))


def _yt_fetch_info(url: str) -> dict:
    cmd = [
        "yt-dlp",
        "--dump-single-json",
        "--no-playlist",
        "--skip-download",
        url,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"yt-dlp failed ({res.returncode}): {res.stderr.strip()[:200]}")
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"yt-dlp returned invalid JSON: {e}")


def _safe_import_name(value: str, fallback: str = "video") -> str:
    cleaned = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned).strip("._-")
    return (cleaned or fallback)[:90]


def _detect_google_drive_write_root() -> Path:
    cloud_root = Path.home() / "Library" / "CloudStorage"
    if not cloud_root.exists():
        raise RuntimeError("Google Drive Desktop no está disponible en este backend")
    candidates = sorted(
        [p for p in cloud_root.iterdir() if p.is_dir() and p.name.startswith("GoogleDrive-")],
        key=lambda p: p.name.lower(),
    )
    if not candidates:
        raise RuntimeError("No se encontró GoogleDrive-*; abre Google Drive Desktop e inicia sesión")
    drive_root = candidates[0]
    for name in ("Mi unidad", "My Drive"):
        write_root = drive_root / name
        if write_root.exists() and write_root.is_dir():
            return write_root
    for child in sorted([p for p in drive_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        if os.access(child, os.W_OK | os.X_OK):
            return child
    raise RuntimeError("No se encontró una carpeta escribible dentro de Google Drive")


def _ensure_drive_subdir(write_root: Path, subdir: str) -> Path:
    clean = str(subdir or "AdmiraNext/Importados").strip().strip("/")
    if not clean:
        clean = "AdmiraNext/Importados"
    dst_dir = (write_root / clean).resolve()
    try:
        dst_dir.relative_to(write_root.resolve())
    except Exception:
        raise RuntimeError("Subcarpeta de Drive inválida")
    dst_dir.mkdir(parents=True, exist_ok=True)
    return dst_dir


IMPORTAR_SHEET_ID = os.environ.get("IMPORTAR_SHEET_ID", "1y3p_avIu-VLqWNU1KON_GoLYVAIxDyHOY4IhgJcTIrk")
IMPORTAR_SHEET_TAB = os.environ.get("IMPORTAR_SHEET_TAB", "Entrenar Links")
IMPORTAR_SHEET_URL = f"https://docs.google.com/spreadsheets/d/{IMPORTAR_SHEET_ID}/edit"
IMPORTAR_SHEET_EXTRA_HEADERS = ["importStatus", "importedAt", "drivePath", "driveUrl", "bytes"]
_import_jobs_lock = threading.Lock()
_import_jobs: dict[str, dict] = {}


def _import_job_update(job_id: str, **fields) -> dict:
    with _import_jobs_lock:
        job = _import_jobs.setdefault(job_id, {})
        job.update(fields)
        job["updatedAt"] = datetime.now().isoformat()
        return dict(job)


def _import_job_snapshot(job_id: str) -> dict:
    with _import_jobs_lock:
        job = _import_jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        return dict(job)


def _drive_search_url(path: Path) -> str:
    return "https://drive.google.com/drive/search?q=" + urllib.parse.quote(path.name)


def _csv_escape_sheet(value) -> str:
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def _col_name(index: int) -> str:
    name = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _get_google_sheet_service():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    try:
        from googleapiclient.discovery import build
        from google.oauth2 import service_account
        import google.auth
    except Exception as e:
        raise RuntimeError(f"Google Sheets API no disponible: {e}")

    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    sa_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if sa_json:
        info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    elif sa_file and Path(sa_file).exists():
        creds = service_account.Credentials.from_service_account_file(sa_file, scopes=scopes)
    else:
        creds, _project = google.auth.default(scopes=scopes)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _queue_imported_training_for_sheet(import_data: dict, queue_dir: Path, reason: str) -> dict:
    queue_dir.mkdir(parents=True, exist_ok=True)
    pending_jsonl = queue_dir / "_importar_sheet_pending.jsonl"
    pending_csv = queue_dir / "_importar_sheet_pending.csv"
    row = {
        "generation": "importado",
        "persona": "",
        "kind": "video",
        "source": "Importar",
        "title": import_data.get("title") or "Vídeo importado",
        "url": import_data.get("sourceUrl") or "",
        "ts": str(int(time.time() * 1000)),
        "createdAt": datetime.now().isoformat(),
        "importStatus": "importado",
        "importedAt": datetime.now().isoformat(),
        "drivePath": import_data.get("drivePath") or "",
        "driveUrl": import_data.get("driveUrl") or "",
        "bytes": str(import_data.get("bytes") or ""),
        "sheetError": reason[:300],
    }
    with pending_jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_header = not pending_csv.exists()
    with pending_csv.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    return {
        "updated": False,
        "queued": True,
        "reason": reason[:300],
        "queuePath": str(pending_csv),
        "sheetUrl": IMPORTAR_SHEET_URL,
    }


def _register_imported_training(import_data: dict, queue_dir: Path) -> dict:
    try:
        service = _get_google_sheet_service()
        sheet = service.spreadsheets().values()
        header_resp = sheet.get(
            spreadsheetId=IMPORTAR_SHEET_ID,
            range=f"'{IMPORTAR_SHEET_TAB}'!1:1",
        ).execute()
        headers = list((header_resp.get("values") or [[]])[0])
        if not headers:
            headers = ["generation", "persona", "kind", "source", "title", "url", "ts", "createdAt"]
        for header in IMPORTAR_SHEET_EXTRA_HEADERS:
            if header not in headers:
                headers.append(header)
        last_col = _col_name(len(headers))
        sheet.update(
            spreadsheetId=IMPORTAR_SHEET_ID,
            range=f"'{IMPORTAR_SHEET_TAB}'!A1:{last_col}1",
            valueInputOption="USER_ENTERED",
            body={"values": [headers]},
        ).execute()

        now = datetime.now().isoformat()
        row_map = {
            "generation": "importado",
            "persona": "",
            "kind": "video",
            "source": "Importar",
            "title": import_data.get("title") or "Vídeo importado",
            "url": import_data.get("sourceUrl") or "",
            "ts": str(int(time.time() * 1000)),
            "createdAt": now,
            "importStatus": "importado",
            "importedAt": now,
            "drivePath": import_data.get("drivePath") or "",
            "driveUrl": import_data.get("driveUrl") or "",
            "bytes": str(import_data.get("bytes") or ""),
        }
        row = [_csv_escape_sheet(row_map.get(header, "")) for header in headers]

        values_resp = sheet.get(
            spreadsheetId=IMPORTAR_SHEET_ID,
            range=f"'{IMPORTAR_SHEET_TAB}'!A2:{last_col}",
        ).execute()
        source_col = headers.index("url") if "url" in headers else -1
        target_row = None
        if source_col >= 0:
            for offset, existing in enumerate(values_resp.get("values") or [], start=2):
                if len(existing) > source_col and existing[source_col] == row_map["url"]:
                    target_row = offset
                    break
        if target_row:
            sheet.update(
                spreadsheetId=IMPORTAR_SHEET_ID,
                range=f"'{IMPORTAR_SHEET_TAB}'!A{target_row}:{last_col}{target_row}",
                valueInputOption="USER_ENTERED",
                body={"values": [row]},
            ).execute()
        else:
            sheet.append(
                spreadsheetId=IMPORTAR_SHEET_ID,
                range=f"'{IMPORTAR_SHEET_TAB}'!A:{last_col}",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            ).execute()
        return {
            "updated": True,
            "queued": False,
            "row": target_row,
            "sheetUrl": IMPORTAR_SHEET_URL,
        }
    except Exception as e:
        return _queue_imported_training_for_sheet(import_data, queue_dir, str(e))


def _download_video_to_drive(url: str, subdir: str = "AdmiraNext/Importados", progress=None) -> dict:
    if not HTTP_URL_RE.search(url):
        raise RuntimeError("La URL debe empezar por http:// o https://")
    if shutil.which("yt-dlp") is None:
        raise RuntimeError("yt-dlp no está instalado en este backend")
    if progress:
        progress(status="preparing", step="Preparando Google Drive", progress=0)
    write_root = _detect_google_drive_write_root()
    dst_dir = _ensure_drive_subdir(write_root, subdir)
    max_size = os.environ.get("IMPORTAR_VIDEO_MAX_SIZE", "750M")

    with tempfile.TemporaryDirectory(prefix="council-importar-") as tmp:
        tmp_dir = Path(tmp)
        output_tpl = str(tmp_dir / "%(title).90s [%(id)s].%(ext)s")
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--max-filesize",
            max_size,
            "-f",
            "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "-o",
            output_tpl,
            "--newline",
            "--print",
            "after_move:filepath",
            url,
        ]
        if progress:
            progress(status="downloading", step="Descargando vídeo", progress=1)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        lines = []
        started = time.time()
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if line:
                lines.append(line)
            if time.time() - started > 1800:
                proc.kill()
                raise RuntimeError("yt-dlp superó el tiempo máximo de descarga")
            match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
            if match and progress:
                pct = max(1, min(95, float(match.group(1))))
                progress(status="downloading", step="Descargando vídeo", progress=round(pct, 1), detail=line[:220])
        returncode = proc.wait(timeout=10)
        if returncode != 0:
            detail = lines[-6:]
            raise RuntimeError("yt-dlp falló: " + " ".join(detail)[:500])
        downloaded = None
        for line in reversed(lines):
            try:
                candidate = Path(line.strip())
                if candidate.exists() and candidate.is_file():
                    downloaded = candidate
                    break
            except OSError:
                continue
        if downloaded is None:
            files = [p for p in tmp_dir.iterdir() if p.is_file()]
            downloaded = max(files, key=lambda p: p.stat().st_size) if files else None
        if downloaded is None or not downloaded.exists():
            raise RuntimeError("yt-dlp no dejó ningún archivo descargado")

        title = downloaded.stem
        suffix = downloaded.suffix or ".mp4"
        dst_name = f"{_safe_import_name(title)}_{int(time.time())}{suffix}"
        dst = dst_dir / dst_name
        if progress:
            progress(status="uploading", step="Copiando a Google Drive", progress=96)
        shutil.copy2(downloaded, dst)
        drive_url = _drive_search_url(dst)
        status = dst.with_suffix(dst.suffix + ".upload_status.txt")
        status.write_text(
            "\n".join([
                "state=UPLOADED",
                f"updated_at={datetime.now().isoformat()}",
                f"source_url={url}",
                f"source={downloaded}",
                f"destination={dst}",
                f"drive_url={drive_url}",
            ]) + "\n",
            encoding="utf-8",
        )
        if progress:
            progress(status="sheet", step="Registrando entreno importado", progress=98)
        return {
            "ok": True,
            "title": title,
            "sourceUrl": url,
            "drivePath": str(dst),
            "driveUrl": drive_url,
            "driveStatus": str(status),
            "driveSubdir": str(dst_dir.relative_to(write_root)),
            "bytes": dst.stat().st_size,
        }


def _run_import_video_job(job_id: str, url: str, subdir: str) -> None:
    try:
        _import_job_update(job_id, status="queued", step="En cola", progress=0)

        def progress(**fields):
            _import_job_update(job_id, **fields)

        result = _download_video_to_drive(url, subdir, progress=progress)
        queue_dir = Path(result["drivePath"]).parent
        sheet_result = _register_imported_training(result, queue_dir)
        result["sheet"] = sheet_result
        _import_job_update(
            job_id,
            status="done",
            step="Importación completada",
            progress=100,
            **result,
        )
    except Exception as e:
        _import_job_update(
            job_id,
            status="error",
            step="Importación fallida",
            progress=0,
            ok=False,
            error=str(e),
        )


def _yt_build_context(info: dict, transcript: str, note: str = "", question: str = "") -> dict:
    title = str(info.get("title") or "YouTube video").strip()
    channel = str(info.get("channel") or info.get("uploader") or "").strip()
    duration = info.get("duration")
    duration_label = ""
    if isinstance(duration, (int, float)) and duration > 0:
        mins = int(duration) // 60
        secs = int(duration) % 60
        duration_label = f"{mins}m {secs:02d}s"
    description = _yt_clean_text(str(info.get("description") or ""))
    description = description[:1800]
    transcript_excerpt = transcript[:14000] if transcript else ""
    meta = [
        f"Título: {title}",
        f"Canal: {channel or 'n/d'}",
        f"Duración: {duration_label or 'n/d'}",
        f"Fecha publicación: {info.get('upload_date') or 'n/d'}",
    ]
    if note:
        meta.append(f"Nota del usuario: {note}")
    if question:
        meta.append(f"Pregunta del usuario: {question}")
    sections = [
        "ANÁLISIS YOUTUBE PRO",
        "\n".join(meta),
    ]
    if description:
        sections.append("DESCRIPCIÓN DEL VIDEO\n" + description)
    if transcript_excerpt:
        sections.append("TRANSCRIPCIÓN / SUBTÍTULOS\n" + transcript_excerpt)
    else:
        sections.append("TRANSCRIPCIÓN / SUBTÍTULOS\nNo disponibles. Analiza apoyándote en metadatos y contexto.")
    prepared = (
        "Analiza este vídeo de YouTube para el Consejo de AdmiraNext. "
        "Quiero: resumen, ideas clave, riesgos, oportunidades, citas o momentos destacables y acciones propuestas.\n\n"
        + "\n\n".join(sections)
    )
    return {
        "title": title,
        "channel": channel,
        "duration": duration,
        "durationLabel": duration_label,
        "description": description,
        "transcriptChars": len(transcript_excerpt),
        "hasTranscript": bool(transcript_excerpt),
        "preparedPrompt": prepared,
    }


@app.post("/api/council/analyze-youtube")
async def council_analyze_youtube(
    req: AnalyzeYoutubeRequest,
    _rate=Depends(check_rate_limit),
    _auth=Depends(verify_token),
):
    url = (req.url or "").strip()
    if not url or not YOUTUBE_RE.search(url):
        raise HTTPException(status_code=400, detail="YouTube URL required")
    loop = asyncio.get_event_loop()

    def build():
        info = _yt_fetch_info(url)
        track = _yt_pick_caption_track(info)
        transcript = _yt_download_caption_text(track) if track else ""
        return _yt_build_context(info, transcript, req.note or "", req.question or "")

    try:
        return await loop.run_in_executor(None, build)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"youtube_analyze failed: {e}")


@app.post("/api/council/importar-video")
async def council_importar_video(
    req: ImportVideoRequest,
    _rate=Depends(check_rate_limit),
    _auth=Depends(verify_token),
):
    url = (req.url or "").strip()
    if not HTTP_URL_RE.search(url):
        raise HTTPException(status_code=400, detail="URL http(s) requerida")
    job_id = uuid.uuid4().hex
    now = datetime.now().isoformat()
    _import_job_update(
        job_id,
        id=job_id,
        ok=True,
        status="queued",
        step="En cola",
        progress=0,
        sourceUrl=url,
        createdAt=now,
        updatedAt=now,
    )
    thread = threading.Thread(
        target=_run_import_video_job,
        args=(job_id, url, req.subdir),
        daemon=True,
        name=f"importar-video-{job_id[:8]}",
    )
    thread.start()
    return _import_job_snapshot(job_id)


@app.get("/api/council/importar-video/{job_id}")
async def council_importar_video_status(
    job_id: str,
    _rate=Depends(check_rate_limit),
    _auth=Depends(verify_token),
):
    try:
        return _import_job_snapshot(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Import job no encontrado")


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

_BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
PRESENTATIONS_DIR = Path(os.environ.get("PRESENTATIONS_DIR", str(_BASE_DIR / "presentations")))
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
# ║  PRESENTAR — Claude genera estructura + audio/PDF/slides     ║
# ╚══════════════════════════════════════════════════════════════╝

class PresentarRequest(BaseModel):
    prompt: str
    file_content: Optional[str] = None
    file_name: Optional[str] = None
    formato: str = "audio"  # audio | pdf | ambos | slides

@app.post("/api/council/presentar")
async def council_presentar(req: PresentarRequest, request: Request):
    """Pipeline: Claude genera contenido estructurado → audio/PDF/slides según formato."""
    check_rate_limit(request)

    # ── 1. Claude genera el documento estructurado ──────────────
    system_prompt = (
        "Eres el secretario del Consejo de Administración de AdmiraNext. "
        "Genera el contenido estructurado de una presentación profesional. "
        "Responde SOLO con JSON válido con esta estructura:\n"
        '{"title":"...","summary":"...","sections":['
        '{"title":"...","content":"...","bullets":["...","..."]}],'
        '"sources":["..."],"conclusion":"..."}\n'
        "Incluye 4-6 secciones. Sé conciso pero completo. Sin texto fuera del JSON."
    )
    user_content = req.prompt
    if req.file_content:
        if req.file_content.startswith("data:"):
            user_content += f"\n\n[Fichero adjunto: {req.file_name or 'archivo'}]"
        else:
            user_content += f"\n\nContenido de '{req.file_name or 'adjunto'}':\n{req.file_content[:8000]}"

    # Usar Groq (llama-3.3-70b-versatile) — gratis, sin consumir tokens de Anthropic
    if not GROQ_API_KEY:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY no configurada — obtén una gratis en console.groq.com")

    groq_resp = http_requests.post(
        GROQ_API_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 4000,
            "temperature": 0.3,
        },
        timeout=60,
    )
    groq_resp.raise_for_status()
    raw = groq_resp.json()["choices"][0]["message"]["content"]

    import re
    try:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        data = json.loads(m.group()) if m else {"title": req.prompt[:60], "sections": [], "summary": raw}
    except Exception:
        data = {"title": req.prompt[:60], "sections": [], "summary": raw}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = re.sub(r'[^\w]', '_', data.get("title", "presentacion"))[:30]

    result: dict = {
        "ok": True,
        "title": data.get("title", "Presentación"),
        "sections": [s.get("title", s) if isinstance(s, dict) else s for s in data.get("sections", [])],
    }

    # ── 2. Generar salidas según formato ────────────────────────
    if req.formato in ("pdf", "ambos"):
        p = _presentar_pdf(data, timestamp, safe_title)
        if p: result["pdf_url"] = f"/presentations/{p.name}"

    if req.formato in ("audio", "ambos"):
        p = _presentar_audio(data, timestamp, safe_title)
        if p: result["audio_url"] = f"/audio/{p.name}"

    if req.formato == "slides":
        p = _presentar_slides(data, timestamp, safe_title)
        if p: result["slides_url"] = f"/presentations/{p.name}"

    # ── 3. Guardar en estado de presentaciones ───────────────────
    state = _presentations_load_state()
    item = {
        "slug": f"{timestamp}_{safe_title}",
        "title": data.get("title", "Presentación"),
        "created_at": datetime.now().isoformat(),
        **{k: result[k] for k in ("audio_url","pdf_url","slides_url") if k in result},
    }
    state["items"] = [item] + state.get("items", [])[:9]
    state["active"] = item["slug"]
    PRESENTATIONS_STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    return result


def _presentar_pdf(data: dict, timestamp: str, safe_title: str) -> "Path | None":
    try:
        from fpdf import FPDF

        def _s(t):
            """Sanitize text to Latin-1 safe string."""
            return (t or "").encode("latin-1", errors="replace").decode("latin-1")

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        pdf.set_font("Helvetica", "B", 20)
        pdf.multi_cell(0, 12, _s(data.get("title", "Presentacion")), align="C")
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 10)
        pdf.multi_cell(0, 8, _s(f"AdmiraNext Council — {datetime.now().strftime('%d/%m/%Y')}"), align="C")
        pdf.ln(6)

        def section_block(heading, body, bullets=None):
            pdf.set_font("Helvetica", "B", 13)
            pdf.multi_cell(0, 8, _s(heading))
            pdf.ln(2)
            pdf.set_font("Helvetica", "", 10)
            if body:
                pdf.multi_cell(0, 6, _s(body))
            for b in (bullets or []):
                pdf.multi_cell(0, 6, _s(f"  - {b}"))
            pdf.ln(4)

        if data.get("summary"):
            section_block("Resumen Ejecutivo", data["summary"])
        for s in data.get("sections", []):
            if isinstance(s, dict):
                section_block(s.get("title", ""), s.get("content", ""), s.get("bullets", []))
        if data.get("conclusion"):
            section_block("Conclusion", data["conclusion"])
        if data.get("sources"):
            section_block("Fuentes", "\n".join(f"- {src}" for src in data["sources"]))

        pdf_path = PRESENTATIONS_DIR / f"{timestamp}_{safe_title}.pdf"
        pdf.output(str(pdf_path))
        return pdf_path
    except Exception as e:
        print(f"PDF error: {e}")
        return None


def _presentar_audio(data: dict, timestamp: str, safe_title: str) -> "Path | None":
    try:
        from gtts import gTTS
        text = f"{data.get('title','')}. {data.get('summary','')} "
        for s in data.get("sections", []):
            if isinstance(s, dict):
                text += f"{s.get('title','')}. {s.get('content','')} "
        text += data.get("conclusion", "")
        text = text[:3000]

        mp3_path = AUDIO_DIR / f"{timestamp}_{safe_title}.mp3"
        tts = gTTS(text=text, lang="es", slow=False)
        tts.save(str(mp3_path))
        return mp3_path
    except Exception as e:
        print(f"Audio error: {e}")
        return None


def _presentar_slides(data: dict, timestamp: str, safe_title: str) -> "Path | None":
    try:
        title   = data.get("title", "Presentación")
        sections = data.get("sections", [])
        total   = len(sections) + 1

        def slide(num, stitle, scontent, bullets=None):
            bl = "".join(f'<div class="bullet">▶ {b}</div>' for b in (bullets or [])[:5])
            return (
                f'<section class="slide">'
                f'<span class="num">{num:02d}/{total:02d}</span>'
                f'<h2>{stitle}</h2>'
                f'<p>{scontent[:400]}</p>{bl}'
                f'</section>\n'
            )

        html = f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<title>{title}</title>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a1e;color:#ffee88;font-family:'Press Start 2P',monospace;scroll-snap-type:y mandatory;overflow-y:scroll;height:100vh}}
.slide{{min-height:100vh;padding:60px 80px;display:flex;flex-direction:column;justify-content:center;scroll-snap-align:start;border-bottom:3px solid #daa520}}
.num{{font-size:7px;color:#554433;margin-bottom:16px}}
h2{{font-size:16px;color:#daa520;margin-bottom:24px;line-height:1.6}}
p{{font-size:9px;line-height:2.2;margin-bottom:16px}}
.bullet{{font-size:8px;padding:8px 0 8px 20px;line-height:1.8;border-bottom:1px solid #1a1a3e}}
.badge{{position:fixed;bottom:12px;right:16px;font-size:6px;color:#332211}}
</style></head><body>
{slide(0, title, data.get('summary','')[:400])}
"""
        for i, s in enumerate(sections):
            if isinstance(s, dict):
                html += slide(i+1, s.get("title",""), s.get("content",""), s.get("bullets",[]))
            else:
                html += slide(i+1, str(s), "")

        if data.get("conclusion"):
            html += slide(total, "Conclusión", data["conclusion"])

        html += f'<div class="badge">AdmiraNext Consejo · {datetime.now().strftime("%d/%m/%Y")}</div></body></html>'

        path = PRESENTATIONS_DIR / f"{timestamp}_{safe_title}_slides.html"
        path.write_text(html, encoding="utf-8")
        return path
    except Exception as e:
        print(f"Slides error: {e}")
        return None


# ╔══════════════════════════════════════════════════════════════╗
# ║  DAILY BOOK — Un consejero pone un libro sobre la mesa       ║
# ╚══════════════════════════════════════════════════════════════╝

DAILY_STATE_DIR = Path(os.environ.get("DAILY_STATE_DIR", str(_BASE_DIR / ".council-daily")))
DAILY_STATE_FILE = DAILY_STATE_DIR / "state.json"
AUDIO_DIR = Path(os.environ.get("AUDIO_DIR", str(_BASE_DIR / "audio")))
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


# ── CREAR: cola de jobs para generación de imágenes ──────────
# Frontend → POST /api/council/crear        (encola)
# Frontend → GET  /api/council/crear/<id>   (polling)
# Agente   → GET  /api/council/crear-pending (lee cola)
# Agente   → POST /api/council/crear/<id>/result {imageUrl} (entrega)
_crear_jobs: dict = {}
_CREAR_JOB_TTL = 3600  # 1h
_CREAR_PROCESSING_TIMEOUT = 900  # 15 min


def _crear_cleanup():
    cutoff = time.time() - _CREAR_JOB_TTL
    now = time.time()
    for job in _crear_jobs.values():
        if (
            job.get("status") == "processing"
            and job.get("startedAt")
            and (now - job["startedAt"]) > _CREAR_PROCESSING_TIMEOUT
        ):
            job["status"] = "pending"
            job.pop("startedAt", None)
            job.pop("workerId", None)
            job["requeuedAt"] = now
    expired = [k for k, j in list(_crear_jobs.items()) if j.get("createdAt", 0) < cutoff]
    for k in expired:
        _crear_jobs.pop(k, None)


def _crear_queue_counts():
    pending_jobs = sorted(
        (j for j in _crear_jobs.values() if j.get("status") == "pending"),
        key=lambda j: j.get("createdAt", 0),
    )
    pending_order = {j["id"]: idx + 1 for idx, j in enumerate(pending_jobs)}
    processing_count = sum(1 for j in _crear_jobs.values() if j.get("status") == "processing")
    return pending_order, len(pending_jobs), processing_count


def _crear_public_job(job: dict):
    pending_order, pending_count, processing_count = _crear_queue_counts()
    out = dict(job)
    out["ageSeconds"] = max(0, int(time.time() - job.get("createdAt", time.time())))
    out["pendingCount"] = pending_count
    out["processingCount"] = processing_count
    out["queuePosition"] = pending_order.get(job["id"]) if job.get("status") == "pending" else None
    return out


class CrearJobRequest(BaseModel):
    prompt: str
    calidad: Optional[str] = None
    gen: Optional[str] = "leyendas"
    ts: Optional[int] = None


class CrearResultRequest(BaseModel):
    imageUrl: str


class CrearErrorRequest(BaseModel):
    error: str


class CrearClaimRequest(BaseModel):
    workerId: Optional[str] = None


class EntrenarItemsRequest(BaseModel):
    items: list[dict]


@app.get("/api/council/entrenar/{gen}")
async def entrenar_get_gen(gen: str, _auth=Depends(verify_token)):
    with _entrenar_lock:
        return _entrenar_gen_snapshot(gen)


@app.get("/api/council/entrenar/{gen}/{persona}")
async def entrenar_get_persona(gen: str, persona: str, _auth=Depends(verify_token)):
    with _entrenar_lock:
        snapshot = _entrenar_gen_snapshot(gen)
    items = snapshot["personas"].get(persona, [])
    return {"gen": gen, "persona": persona, "items": items, "count": len(items)}


@app.post("/api/council/entrenar/{gen}/{persona}/merge")
async def entrenar_merge_persona(gen: str, persona: str, req: EntrenarItemsRequest, _auth=Depends(verify_token)):
    with _entrenar_lock:
        store = _load_entrenar_store()
        gen_bucket = store.setdefault(gen, {})
        if not isinstance(gen_bucket, dict):
            gen_bucket = {}
            store[gen] = gen_bucket
        existing = gen_bucket.get(persona, [])
        merged = _merge_entrenar_items(existing if isinstance(existing, list) else [], req.items)
        gen_bucket[persona] = merged
        _save_entrenar_store(store)
    return {"gen": gen, "persona": persona, "items": merged, "count": len(merged)}


@app.post("/api/council/crear")
async def crear_enqueue(req: CrearJobRequest, _auth=Depends(verify_token)):
    _crear_cleanup()
    job_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
    _crear_jobs[job_id] = {
        "id": job_id,
        "prompt": req.prompt,
        "calidad": req.calidad,
        "gen": req.gen,
        "createdAt": time.time(),
        "status": "pending",
    }
    return _crear_public_job(_crear_jobs[job_id])


@app.get("/api/council/crear/{job_id}")
async def crear_status(job_id: str, _auth=Depends(verify_token)):
    _crear_cleanup()
    job = _crear_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found or expired")
    return _crear_public_job(job)


@app.get("/api/council/crear-pending")
async def crear_list_pending(_auth=Depends(verify_token)):
    _crear_cleanup()
    pending = sorted(
        (j for j in _crear_jobs.values() if j["status"] == "pending"),
        key=lambda j: j["createdAt"],
    )
    return {"jobs": [_crear_public_job(j) for j in pending]}


@app.post("/api/council/crear/claim")
async def crear_claim(req: CrearClaimRequest, _auth=Depends(verify_token)):
    _crear_cleanup()
    pending = sorted(
        (j for j in _crear_jobs.values() if j["status"] == "pending"),
        key=lambda j: j["createdAt"],
    )
    if not pending:
        return {"job": None}
    job = pending[0]
    job["status"] = "processing"
    job["startedAt"] = time.time()
    job["workerId"] = (req.workerId or "anonymous-worker")[:120]
    return {"job": _crear_public_job(job)}


@app.post("/api/council/crear/{job_id}/result")
async def crear_set_result(job_id: str, req: CrearResultRequest, _auth=Depends(verify_token)):
    job = _crear_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found or expired")
    job["status"] = "done"
    job["imageUrl"] = req.imageUrl
    job["completedAt"] = time.time()
    return _crear_public_job(job)


@app.post("/api/council/crear/{job_id}/error")
async def crear_set_error(job_id: str, req: CrearErrorRequest, _auth=Depends(verify_token)):
    job = _crear_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found or expired")
    job["status"] = "error"
    job["error"] = req.error[:500]
    job["completedAt"] = time.time()
    return _crear_public_job(job)


# ─────────────────────────────────────────────────────────────
# HACKEO — ping + SSH (encendidas) / Wake-on-LAN (apagadas)
# ─────────────────────────────────────────────────────────────
# Para cada máquina del consejo:
#   1. Ping por Tailscale (hostname.tail48b61c.ts.net)
#   2. Si responde → SSH lanza simulación de hackeo en su Terminal
#   3. Si no responde → envía paquete Wake-on-LAN (requiere mac_address
#      en data/machines.json — RELLENAR los campos vacíos para que WoL
#      funcione de verdad; sin MAC el backend devuelve action="skipped").
import socket as _hk_socket
from concurrent.futures import ThreadPoolExecutor as _HkPool

_HK_MACHINES_PATH = Path(__file__).parent / "data" / "machines.json"
_HK_SSH_TIMEOUT = 4
_HK_PING_TIMEOUT = 2

# Script Python que se ejecuta DENTRO del Terminal remoto: imprime
# líneas tipo "hackeo en curso" hasta que se cierra el terminal.
_HK_REMOTE_PY = r'''
import time, random, sys, socket
LINES = [
 "$ ssh -o StrictHostKeyChecking=no root@10.0.0.1",
 "Connecting to 10.0.0.1:22...",
 "Authenticating with stolen RSA key...",
 "ACCESS GRANTED -- Welcome to "+socket.gethostname(),
 "$ sudo cat /etc/shadow",
 "root:$6$xQ9Z..redacted:19471:0:99999:7:::",
 "$ find / -name '*.pem' -o -name '*.key' 2>/dev/null",
 "/etc/ssl/private/server.key",
 "$ mysqldump --all-databases > /tmp/dump.sql",
 "Dumping database 'admira_prod'... [OK] 847 tables (312MB)",
 "$ nmap -sS -p 1-65535 100.74.101.14",
 "PORT      STATE  SERVICE",
 "22/tcp    open   ssh",
 "443/tcp   open   https",
 "$ echo 'Exfiltrating data...'",
 "Uploading dump.sql to c2.server... [=====>] 100%",
 "$ history -c && echo '' > ~/.bash_history",
 "Tracks cleared.",
]
i = 0
try:
    while True:
        line = LINES[i % len(LINES)]
        for c in line:
            sys.stdout.write(c); sys.stdout.flush()
            time.sleep(random.uniform(0.005, 0.04))
        sys.stdout.write("\n"); sys.stdout.flush()
        i += 1
        time.sleep(random.uniform(0.15, 0.5))
except (KeyboardInterrupt, BrokenPipeError):
    pass
'''


def _hk_load_council() -> list:
    """Devuelve solo las máquinas con unitType=='council' y SSH habilitado."""
    try:
        data = json.loads(_HK_MACHINES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for m in data.get("machines", []):
        if m.get("unitType") != "council":
            continue
        ssh = m.get("ssh") or {}
        if not ssh.get("enabled"):
            continue
        if not ssh.get("host"):
            continue
        out.append(m)
    return out


def _hk_ping(host: str) -> bool:
    """Ping ICMP rápido por hostname Tailscale. True si responde."""
    if not host:
        return False
    try:
        # macOS / Linux: ping -c 1 -W 1000 (ms en macOS, s en Linux)
        # Usamos -c1 -W1; si falla, devolvemos False sin lanzar.
        cmd = ["ping", "-c", "1", "-W", "1", host]
        r = subprocess.run(cmd, capture_output=True, timeout=_HK_PING_TIMEOUT)
        return r.returncode == 0
    except Exception:
        return False


def _hk_ssh_launch(user: str, host: str) -> tuple:
    """Lanza la simulación de hackeo en el Terminal del Mac remoto.

    Usa osascript para abrir Terminal.app y arranca el script Python
    embebido. Devuelve (ok, detail).
    """
    if not user or not host:
        return False, "missing ssh user/host"
    # Nota: el script va base64 para no pelearse con escapes a través de
    # ssh + osascript + AppleScript + bash.
    # `caffeinate -u -t 2 && sleep 1` despierta el display y desactiva
    # el screensaver durante 2s antes de lanzar osascript: si la GUI
    # está bloqueada, Terminal.app no puede recibir AppleEvents.
    import base64 as _b64
    payload = _b64.b64encode(_HK_REMOTE_PY.encode("utf-8")).decode("ascii")
    remote_cmd = (
        "caffeinate -u -t 2 && sleep 1 && "
        "osascript -e 'tell application \"Terminal\" to activate' "
        "-e 'tell application \"Terminal\" to do script "
        f"\"clear; echo \\\"== ADMIRA HACK SIMULATION ==\\\"; "
        f"echo {payload} | base64 -D | python3 -\"'"
    )
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={_HK_SSH_TIMEOUT}",
        f"{user}@{host}",
        remote_cmd,
    ]
    try:
        r = subprocess.run(ssh_cmd, capture_output=True, timeout=_HK_SSH_TIMEOUT + 2)
        if r.returncode == 0:
            return True, "ssh launched"
        err = (r.stderr or b"").decode("utf-8", "ignore").strip()[:200]
        return False, f"ssh rc={r.returncode}: {err or 'no stderr'}"
    except subprocess.TimeoutExpired:
        return False, "ssh timeout"
    except Exception as e:
        return False, f"ssh error: {e}"


def _hk_ssh_stop(user: str, host: str) -> tuple:
    """Cierra el Terminal en el Mac remoto."""
    if not user or not host:
        return False, "missing ssh user/host"
    remote_cmd = "osascript -e 'tell application \"Terminal\" to quit' >/dev/null 2>&1; true"
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={_HK_SSH_TIMEOUT}",
        f"{user}@{host}",
        remote_cmd,
    ]
    try:
        r = subprocess.run(ssh_cmd, capture_output=True, timeout=_HK_SSH_TIMEOUT + 2)
        return r.returncode == 0, "ssh stop ok" if r.returncode == 0 else f"ssh rc={r.returncode}"
    except Exception as e:
        return False, f"ssh stop error: {e}"


def _hk_normalize_mac(mac: str) -> str:
    """Normaliza una MAC a `aa:bb:cc:dd:ee:ff` (lowercase). '' si inválida."""
    raw = re.sub(r"[^0-9a-fA-F]", "", mac or "").lower()
    if len(raw) != 12:
        return ""
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def _hk_arp_lookup(target: str) -> str:
    """Busca la MAC asociada a `target` (host o IP) en la tabla ARP local."""
    if not target:
        return ""
    try:
        r = subprocess.run(["arp", "-n", target], capture_output=True, timeout=2)
        txt = (r.stdout or b"").decode("utf-8", "ignore")
    except Exception:
        return ""
    m = re.search(r"([0-9a-fA-F]{1,2}(?::[0-9a-fA-F]{1,2}){5})", txt)
    return _hk_normalize_mac(m.group(1)) if m else ""


def _hk_discover_mac(machine: dict) -> tuple:
    """Intenta descubrir la MAC del Mac remoto. Devuelve (mac, source).

    Estrategia:
      1. Si hay `host_local` (ej. MacBookProNegro14.local) → ping mDNS y
         leer la tabla ARP local. Solo funciona si el host que ejecuta
         este backend está en la MISMA LAN física que la máquina remota
         (las IPs Tailscale 100.x.x.x no aparecen en ARP).
      2. SSH al host Tailscale y preguntarle su propia MAC con
         `route get default` + `ifconfig <iface>`. Funciona siempre
         que SSH responda — que es justo cuando queremos descubrirla.
    """
    ssh = machine.get("ssh") or {}
    user = ssh.get("user", "")
    host = ssh.get("host", "")
    host_local = ssh.get("host_local", "")
    ip_local = ssh.get("ip_local", "")

    # 1) ARP local (mDNS .local o IP LAN)
    for tgt in (host_local, ip_local):
        if not tgt:
            continue
        try:
            subprocess.run(["ping", "-c", "1", "-W", "1", tgt],
                           capture_output=True, timeout=2)
        except Exception:
            pass
        mac = _hk_arp_lookup(tgt)
        if mac:
            return mac, f"arp:{tgt}"

    # 2) SSH ifconfig (cubre el caso Tailscale-only)
    if user and host:
        remote = (
            "iface=$(route -n get default 2>/dev/null | awk '/interface:/{print $2}'); "
            "[ -z \"$iface\" ] && iface=en0; "
            "ifconfig \"$iface\" 2>/dev/null | awk '/ether/{print $2; exit}'"
        )
        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={_HK_SSH_TIMEOUT}",
            f"{user}@{host}",
            remote,
        ]
        try:
            r = subprocess.run(ssh_cmd, capture_output=True, timeout=_HK_SSH_TIMEOUT + 2)
            raw = (r.stdout or b"").decode("utf-8", "ignore").strip()
            mac = _hk_normalize_mac(raw)
            if mac:
                return mac, "ssh:ifconfig"
        except Exception:
            pass

    return "", ""


_HK_SAVE_LOCK = threading.Lock()


def _hk_save_macs(updates: dict) -> int:
    """Persiste las MACs descubiertas en machines.json (escritura atómica).

    `updates` es un dict {machine_id: mac}. Solo se sobrescriben las
    entradas cuya MAC ha cambiado. Devuelve el nº de entradas modificadas.
    """
    if not updates:
        return 0
    with _HK_SAVE_LOCK:
        try:
            data = json.loads(_HK_MACHINES_PATH.read_text(encoding="utf-8"))
        except Exception:
            return 0
        n = 0
        now = datetime.utcnow().isoformat() + "Z"
        for m in data.get("machines", []):
            mid = m.get("id")
            if mid in updates and updates[mid]:
                if (m.get("mac_address") or "") != updates[mid]:
                    m["mac_address"] = updates[mid]
                    m["mac_discovered_at"] = now
                    n += 1
        if n == 0:
            return 0
        data["updatedAt"] = now
        tmp = _HK_MACHINES_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, _HK_MACHINES_PATH)
        return n


def _hk_send_wol(mac: str) -> tuple:
    """Envía un magic packet Wake-on-LAN al MAC dado (broadcast UDP)."""
    if not mac:
        return False, "no mac_address (RELLENAR en data/machines.json)"
    raw = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(raw) != 12:
        return False, f"mac inválida: {mac!r}"
    try:
        mac_bytes = bytes.fromhex(raw)
        packet = b"\xff" * 6 + mac_bytes * 16
        sock = _hk_socket.socket(_hk_socket.AF_INET, _hk_socket.SOCK_DGRAM)
        sock.setsockopt(_hk_socket.SOL_SOCKET, _hk_socket.SO_BROADCAST, 1)
        # Enviamos al broadcast directo y al subnet local; ambos son
        # comunes según cómo esté la red.
        for addr in ("255.255.255.255", "<broadcast>"):
            try:
                sock.sendto(packet, (addr, 9))
                sock.sendto(packet, (addr, 7))
            except Exception:
                pass
        sock.close()
        return True, f"wol sent to {mac}"
    except Exception as e:
        return False, f"wol error: {e}"


def _hk_process_one(machine: dict, action: str) -> dict:
    ssh = machine.get("ssh") or {}
    host = ssh.get("host", "")
    user = ssh.get("user", "")
    mac = machine.get("mac_address", "")
    name = machine.get("name") or machine.get("id")

    alive = _hk_ping(host)
    result = {
        "id": machine.get("id"),
        "name": name,
        "host": host,
        "online": alive,
        "action": "none",
        "ok": False,
        "detail": "",
    }

    if action == "stop":
        if alive:
            ok, detail = _hk_ssh_stop(user, host)
            result["action"] = "ssh_stop"
            result["ok"] = ok
            result["detail"] = detail
        else:
            result["action"] = "skipped"
            result["detail"] = "offline, nothing to stop"
            result["ok"] = True
        return result

    if alive:
        # Si está encendida y aún no tenemos su MAC, la descubrimos ahora
        # (ARP local + SSH ifconfig) para poder hacerle WoL la próxima vez
        # aunque esté apagada. El endpoint la persistirá en machines.json.
        if not mac:
            disc_mac, disc_src = _hk_discover_mac(machine)
            if disc_mac:
                result["discovered_mac"] = disc_mac
                result["mac_source"] = disc_src
        ok, detail = _hk_ssh_launch(user, host)
        result["action"] = "ssh_launched"
        result["ok"] = ok
        result["detail"] = detail
    else:
        ok, detail = _hk_send_wol(mac)
        result["action"] = "wol_sent" if ok else "wol_skipped"
        result["ok"] = ok
        result["detail"] = detail
    return result


@app.post("/api/council/hackeo")
async def council_hackeo(_auth=Depends(verify_token)):
    """Lanza la simulación de hackeo en cada máquina del consejo.

    Para cada consejero: ping → si vive, SSH; si no, Wake-on-LAN.
    Devuelve el estado por máquina para que el frontend pinte el resultado.
    """
    machines = _hk_load_council()
    if not machines:
        return {"ok": False, "error": "no council machines", "machines": []}

    results: list = []
    with _HkPool(max_workers=min(8, len(machines))) as pool:
        for r in pool.map(lambda m: _hk_process_one(m, "start"), machines):
            results.append(r)

    # Persistir las MACs que se hayan descubierto en este lanzamiento.
    updates = {r["id"]: r["discovered_mac"] for r in results if r.get("discovered_mac")}
    saved = _hk_save_macs(updates) if updates else 0

    summary = {
        "total": len(results),
        "online": sum(1 for r in results if r["online"]),
        "offline": sum(1 for r in results if not r["online"]),
        "ssh_ok": sum(1 for r in results if r["action"] == "ssh_launched" and r["ok"]),
        "wol_sent": sum(1 for r in results if r["action"] == "wol_sent"),
        "failed": sum(1 for r in results if not r["ok"]),
        "macs_discovered": saved,
    }
    return {
        "ok": True,
        "version": "Admira v.26.05.07.r6",
        "ts": datetime.utcnow().isoformat() + "Z",
        "summary": summary,
        "machines": results,
    }


@app.post("/api/council/hackeo/stop")
async def council_hackeo_stop(_auth=Depends(verify_token)):
    """Detiene la simulación cerrando Terminal en cada máquina viva."""
    machines = _hk_load_council()
    results: list = []
    with _HkPool(max_workers=min(8, max(1, len(machines)))) as pool:
        for r in pool.map(lambda m: _hk_process_one(m, "stop"), machines):
            results.append(r)
    return {
        "ok": True,
        "version": "Admira v.26.05.07.r6",
        "ts": datetime.utcnow().isoformat() + "Z",
        "machines": results,
    }


def _hk_discover_one(machine: dict) -> dict:
    ssh = machine.get("ssh") or {}
    host = ssh.get("host", "")
    alive = _hk_ping(host)
    out = {
        "id": machine.get("id"),
        "name": machine.get("name") or machine.get("id"),
        "host": host,
        "online": alive,
        "mac_before": machine.get("mac_address", ""),
        "mac": "",
        "source": "",
    }
    if alive:
        mac, src = _hk_discover_mac(machine)
        out["mac"] = mac
        out["source"] = src
    return out


@app.post("/api/council/hackeo/discover-macs")
async def council_hackeo_discover_macs(_auth=Depends(verify_token)):
    """Autodescubre las MAC de los Macs encendidos y las persiste en
    machines.json para que la próxima vez podamos despertarlos por
    Wake-on-LAN aunque estén apagados.

    No lanza la simulación de hackeo; solo descubre y guarda.
    """
    machines = _hk_load_council()
    if not machines:
        return {"ok": False, "error": "no council machines", "machines": []}

    results: list = []
    with _HkPool(max_workers=min(8, len(machines))) as pool:
        for r in pool.map(_hk_discover_one, machines):
            results.append(r)

    updates = {r["id"]: r["mac"] for r in results if r["mac"]}
    saved = _hk_save_macs(updates) if updates else 0

    return {
        "ok": True,
        "version": "Admira v.26.05.07.r6",
        "ts": datetime.utcnow().isoformat() + "Z",
        "summary": {
            "total": len(results),
            "online": sum(1 for r in results if r["online"]),
            "discovered": len(updates),
            "saved": saved,
        },
        "machines": results,
    }


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
          f"Groq (Llama/DeepSeek/Gemma) {'✅' if GROQ_API_KEY else '⚠️  Set GROQ_API_KEY in .env'} | "
          f"Gemini {'✅' if GOOGLE_API_KEY else '⚠️  Set GOOGLE_API_KEY in .env (aistudio.google.com)'}")
    uvicorn.run(app, host="0.0.0.0", port=8420)
