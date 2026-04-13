"""
Council API Bridge — Conecta el frontend SCUMM con los agentes del Consejo AdmiraNext.
Usa FastAPI + Anthropic SDK para que cada consejero responda como su persona.
"""

import sys
import os
import asyncio
from typing import Optional

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)

from fastapi import FastAPI
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

# ── App ──────────────────────────────────────────────────────
app = FastAPI(title="AdmiraNext Council API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# Cache instantiated agents
_agent_cache: dict[str, CouncilAgent] = {}


def get_agent(cls) -> CouncilAgent:
    key = f"{cls.__module__}.{cls.__name__}"
    if key not in _agent_cache:
        _agent_cache[key] = cls(client=client)
    return _agent_cache[key]


# ── Models ───────────────────────────────────────────────────
class AskRequest(BaseModel):
    message: str
    generation: str = "leyendas"  # "leyendas" or "coetaneos"
    context: Optional[list[dict]] = None  # Previous conversation messages


class AgentReply(BaseModel):
    name: str
    role: str
    persona: str
    side: str
    icon: str
    content: str


class AskResponse(BaseModel):
    racional: list[AgentReply]
    creativo: list[AgentReply]


# Icon map matching the SCUMM frontend
ICONS = {
    "CEO": "🏛️", "CTO": "⚙️", "COO": "📋", "CFO": "💰",
    "CCO": "💡", "CDO": "🎨", "CXO": "🌐", "CSO": "📖",
}


def agent_ask(agent: CouncilAgent, message: str, context: Optional[list]) -> str:
    """Call Claude with the agent's persona for a conversational response."""
    messages = []

    # Add conversation context if provided
    if context:
        for msg in context[-6:]:  # Last 6 messages max
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

    # Add the current user message
    messages.append({"role": "user", "content": message})

    # Enhance system prompt for conversational mode
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
    return response.content[0].text


# ── Endpoints ────────────────────────────────────────────────
@app.post("/api/council/ask", response_model=AskResponse)
async def council_ask(req: AskRequest):
    """Send a message to the council. All 8 agents respond concurrently."""
    gen = req.generation if req.generation in AGENTS else "leyendas"
    group = AGENTS[gen]

    # Run agents in batches to respect rate limits (5 req/min on free tier)
    # Batch size of 4 with a pause between batches
    loop = asyncio.get_event_loop()

    async def run_agent(cls):
        agent = get_agent(cls)
        content = await loop.run_in_executor(
            None, agent_ask, agent, req.message, req.context
        )
        return AgentReply(
            name=agent.name,
            role=agent.role,
            persona=agent.persona,
            side=agent.side,
            icon=ICONS.get(agent.name, "🎯"),
            content=content,
        )

    all_agents = list(group["racional"]) + list(group["creativo"])
    all_replies = []
    BATCH_SIZE = 4
    BATCH_DELAY = 65  # seconds between batches to respect 5 req/min

    for i in range(0, len(all_agents), BATCH_SIZE):
        batch = all_agents[i:i + BATCH_SIZE]
        tasks = [run_agent(cls) for cls in batch]
        batch_replies = await asyncio.gather(*tasks)
        all_replies.extend(batch_replies)
        # Wait between batches if there are more to process
        if i + BATCH_SIZE < len(all_agents):
            await asyncio.sleep(BATCH_DELAY)

    racional_replies = [r for r in all_replies if r.side == "racional"]
    creativo_replies = [r for r in all_replies if r.side == "creativo"]

    return AskResponse(racional=racional_replies, creativo=creativo_replies)


@app.get("/api/council/health")
async def health():
    return {"status": "ok", "agents": 16}


# ── Run ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("🏛️  AdmiraNext Council API — http://localhost:8420")
    uvicorn.run(app, host="0.0.0.0", port=8420)
