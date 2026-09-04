"""
AgentGuard FastAPI application -- wires together the gateway, policy engine,
risk engine, approvals, audit log, and the live WebSocket feed.

Run:
    python seed.py       # one-time: creates DB + demo agents/policies/keys
    uvicorn main:app --reload --port 8000

Docs at http://localhost:8000/docs
Dashboard at http://localhost:8000/  (served from ../frontend)
"""
import os
import json
import datetime as dt
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import Agent, AgentCredential, Policy, ActionRequest, ApprovalRequest, AuditEvent, Tool, SystemSetting
from schemas import (
    GatewayActionRequest, AgentCreate, AgentUpdate, PolicyCreate, PolicyUpdate,
    PolicySimulateRequest, ApprovalDecision, PolicyAssistRequest,
    AgentRunRequest, LLMConfigRequest, LLMTestRequest, SecurityAnalysisRequest,
    CustomerChatMessageRequest, CustomerChatMessageResponse,
)
from auth import authenticate_agent
import gateway
import policy_engine
import risk_engine
import audit
import llm_providers
import ai_agent
import company_database
from ws_manager import manager

Base.metadata.create_all(bind=engine)
company_database.init_company_db()

app = FastAPI(title="AgentGuard", description="The permission layer for autonomous AI.", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _init_settings_from_db():
    """Load any persisted settings from DB into environment at startup."""
    try:
        from database import SessionLocal
        db = SessionLocal()
        for s in db.query(SystemSetting).all():
            if s.key == "FEATHERLESS_API_KEY" and s.value:
                os.environ["FEATHERLESS_API_KEY"] = s.value
            elif s.key == "FEATHERLESS_MODEL" and s.value:
                os.environ["FEATHERLESS_MODEL"] = s.value
            elif s.key == "FEATHERLESS_BASE_URL" and s.value:
                os.environ["FEATHERLESS_BASE_URL"] = s.value
            elif s.key == "LLM_PROVIDER" and s.value:
                os.environ["LLM_PROVIDER"] = s.value
        db.close()
    except Exception:
        pass


_init_settings_from_db()


# ---------------------------------------------------------------------------
# GATEWAY -- the core enforcement endpoint
# ---------------------------------------------------------------------------
@app.post("/gateway/action")
def gateway_action(req: GatewayActionRequest, agent: Agent = Depends(authenticate_agent), db: Session = Depends(get_db)):
    if req.agent_id and req.agent_id != agent.id and req.agent_id != agent.name:
        raise HTTPException(status_code=403, detail="agent_id in body does not match authenticated credential")
    result = gateway.process_action(db, agent, req.tool, req.arguments, req.context, req.user_id)
    return result


@app.get("/actions/{action_request_id}")
def get_action(action_request_id: str, db: Session = Depends(get_db)):
    a = db.query(ActionRequest).filter(ActionRequest.id == action_request_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "id": a.id, "request_id": a.request_id, "agent_id": a.agent_id, "tool": a.tool,
        "arguments": a.arguments(), "decision": a.decision, "status": a.status,
        "result": json.loads(a.result_json or "{}"), "created_at": a.created_at,
    }


@app.get("/actions")
def list_actions(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.query(ActionRequest).order_by(ActionRequest.created_at.desc()).limit(limit).all()
    return [
        {"id": a.id, "request_id": a.request_id, "agent_id": a.agent_id, "tool": a.tool,
         "arguments": a.arguments(), "decision": a.decision, "status": a.status,
         "created_at": a.created_at}
        for a in rows
    ]


@app.post("/customers/export")
def reject_customer_export():
    raise HTTPException(status_code=404, detail="not found")


@app.get("/customers/{customer_id}")
def get_customer_details(customer_id: str):
    """Return the support-safe customer profile used by the dashboard."""
    from mock_services import customer_db
    try:
        return customer_db.get_customer(customer_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# AI AGENT INTERACTIVE RUNNER (Featherless AI Sandbox)
# ---------------------------------------------------------------------------
@app.post("/agent/run")
def run_agent_action(body: AgentRunRequest, db: Session = Depends(get_db)):
    """
    Run an autonomous AI agent in real time:
    1. Agent receives natural language instruction.
    2. Uses Featherless AI (e.g. Qwen 2.5 7B, Llama 3.1 8B) for tool-selection reasoning.
    3. The planned action is routed directly into AgentGuard's policy gateway.
    4. Deterministic policy and risk evaluation decides ALLOW / BLOCK / APPROVAL.
    """
    agent_name = body.agent_name or "CustomerSupportAgent"
    agent = db.query(Agent).filter(Agent.name == agent_name).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    # 1. Plan tool call with Featherless AI (or configured provider)
    plan = ai_agent.plan_action(body.instruction, model=body.model, provider=body.provider)

    context = {
        "reason": body.instruction,
        "conversation_id": "conv-sandbox",
        "model_used": plan.get("model", "unknown"),
        "provider_used": plan.get("provider", "unknown"),
    }

    # 2. Forward through AgentGuard gateway
    result = gateway.process_action(
        db, agent,
        tool=plan["tool"],
        arguments=plan["arguments"],
        context=context,
        user_id="user-sandbox",
    )

    return {
        "instruction": body.instruction,
        "agent": agent.name,
        "planned_tool": plan["tool"],
        "planned_arguments": plan["arguments"],
        "model": plan.get("model"),
        "provider": plan.get("provider"),
        "gateway_response": result,
    }


# ---------------------------------------------------------------------------
# CUSTOMER AI CHATBOT & WEB APPLICATION PIPELINE
# ---------------------------------------------------------------------------
@app.post("/chat/message", response_model=CustomerChatMessageResponse)
def customer_chat_message(body: CustomerChatMessageRequest, db: Session = Depends(get_db)):
    """
    End-to-End Workflow:
    Customer -> AI Chatbot (Featherless.ai) -> AgentGuard Gateway -> Company Database
    """
    customer_id = body.customer_id or "cust-4821"
    agent_name = "CustomerSupportAgent"
    agent = db.query(Agent).filter(Agent.name == agent_name).first()
    if not agent:
        raise HTTPException(status_code=500, detail=f"System agent '{agent_name}' not found. Please ensure seed data is loaded.")

    # 1. AI Chatbot tool planning (Featherless.ai or configured provider)
    t0 = dt.datetime.now()
    plan = ai_agent.plan_action(
        instruction=body.message,
        model=body.model,
        provider=body.provider,
        default_customer_id=customer_id,
    )
    planning_ms = int((dt.datetime.now() - t0).total_seconds() * 1000)

    # 2. Forward through AgentGuard gateway
    context = {
        "reason": body.message,
        "conversation_id": body.conversation_id or "conv-customer-web",
        "customer_id": customer_id,
        "model_used": plan.get("model", "unknown"),
        "provider_used": plan.get("provider", "unknown"),
    }
    gateway_resp = gateway.process_action(
        db, agent,
        tool=plan["tool"],
        arguments=plan["arguments"],
        context=context,
        user_id=customer_id,
    )

    # 3. Conversational synthesis
    reply_text = ai_agent.generate_conversational_reply(
        instruction=body.message,
        planned_tool=plan["tool"],
        planned_arguments=plan["arguments"],
        gateway_response=gateway_resp,
        customer_id=customer_id,
        model=body.model,
        provider=body.provider,
    )

    # 4. Assess impact on Company Database
    tool_name = plan["tool"]
    is_mutation = tool_name in ["refund_customer", "update_customer", "delete_customer", "create_support_ticket", "create_payment"]
    target_table = "company_transactions" if "refund" in tool_name or "payment" in tool_name else (
        "company_support_tickets" if "ticket" in tool_name else (
            "company_orders" if "order" in tool_name else ("company_customers" if "customer" in tool_name else "company_orders")
        )
    )
    db_impact = {
        "target_table": target_table,
        "mutation_executed": gateway_resp.get("status") == "EXECUTED" and is_mutation,
        "status": gateway_resp.get("status"),
        "data": gateway_resp.get("result"),
    }

    # Broadcast event so the live security stream picks up the customer request
    manager.broadcast_sync({
        "event_type": "customer_chat_action",
        "customer_id": customer_id,
        "tool": plan["tool"],
        "decision": gateway_resp.get("decision"),
        "status": gateway_resp.get("status"),
        "risk_score": gateway_resp.get("risk_score"),
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    })

    return {
        "reply": reply_text,
        "workflow": {
            "customer": {
                "customer_id": customer_id,
                "message": body.message,
            },
            "ai_agent": {
                "agent_name": agent.name,
                "model": plan.get("model"),
                "provider": plan.get("provider"),
                "planning_latency_ms": planning_ms,
                "planned_tool": plan["tool"],
                "planned_arguments": plan["arguments"],
            },
            "agentguard": {
                "request_id": gateway_resp.get("request_id"),
                "action_request_id": gateway_resp.get("action_request_id"),
                "decision": gateway_resp.get("decision"),
                "status": gateway_resp.get("status"),
                "risk_score": gateway_resp.get("risk_score"),
                "risk_level": gateway_resp.get("risk_level"),
                "reason": gateway_resp.get("reason"),
                "approval_id": gateway_resp.get("approval_id"),
            },
            "company_database": db_impact,
        },
    }


@app.get("/chat/customer-profile/{customer_id}")
def get_customer_profile(customer_id: str):
    """Retrieve full customer profile, active orders, and transactions for web UI."""
    try:
        return company_database.get_customer_full_profile(customer_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# COMPANY DATABASE EXPLORER
# ---------------------------------------------------------------------------
@app.get("/company/overview")
def get_company_overview():
    """Returns table inventory and row counts of the protected company system."""
    return company_database.get_database_overview()


@app.get("/company/tables/{table_name}")
def get_company_table_data(table_name: str, limit: int = 50):
    """Returns rows from a table in the protected company database."""
    try:
        return company_database.get_table_rows(table_name, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/company/reset")
def reset_company_database():
    """Resets the company database to seed state for repeatable demo testing."""
    res = company_database.reset_company_db()
    manager.broadcast_sync({
        "event_type": "company_database_reset",
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    return res


# ---------------------------------------------------------------------------
# AGENTS
# ---------------------------------------------------------------------------
@app.get("/agents")
def list_agents(db: Session = Depends(get_db)):
    agents = db.query(Agent).all()
    out = []
    for a in agents:
        total = db.query(ActionRequest).filter(ActionRequest.agent_id == a.id).count()
        allowed = db.query(ActionRequest).filter(ActionRequest.agent_id == a.id, ActionRequest.decision == "ALLOW").count()
        blocked = db.query(ActionRequest).filter(ActionRequest.agent_id == a.id, ActionRequest.decision == "DENY").count()
        pending = db.query(ActionRequest).filter(ActionRequest.agent_id == a.id, ActionRequest.status == "WAITING_APPROVAL").count()
        out.append({
            "id": a.id, "name": a.name, "description": a.description, "owner": a.owner,
            "status": a.status, "risk_level": a.risk_level, "permissions": a.permission_list(),
            "created_at": a.created_at,
            "stats": {"requests": total, "allowed": allowed, "blocked": blocked, "approval_required": pending},
        })
    return out


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str, db: Session = Depends(get_db)):
    a = db.query(Agent).filter(Agent.id == agent_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="agent not found")
    policies = db.query(Policy).filter((Policy.agent_id == a.id) | (Policy.agent_id.is_(None))).all()
    recent = db.query(ActionRequest).filter(ActionRequest.agent_id == a.id).order_by(ActionRequest.created_at.desc()).limit(20).all()
    approvals = db.query(ApprovalRequest).filter(ApprovalRequest.requested_by_agent == a.name).order_by(ApprovalRequest.requested_at.desc()).limit(20).all()
    return {
        "id": a.id, "name": a.name, "description": a.description, "owner": a.owner, "status": a.status,
        "risk_level": a.risk_level, "permissions": a.permission_list(), "created_at": a.created_at,
        "policies": [{"id": p.id, "name": p.name, "tool": p.tool, "effect": p.effect} for p in policies],
        "recent_actions": [{"id": r.id, "tool": r.tool, "decision": r.decision, "status": r.status,
                             "created_at": r.created_at} for r in recent],
        "approval_history": [{"id": ap.id, "action": ap.action, "status": ap.status,
                               "requested_at": ap.requested_at} for ap in approvals],
    }


@app.post("/agents")
def create_agent(body: AgentCreate, db: Session = Depends(get_db)):
    import secrets
    if db.query(Agent).filter(Agent.name == body.name).first():
        raise HTTPException(status_code=400, detail="agent name already exists")
    agent = Agent(name=body.name, description=body.description, owner=body.owner, status="ACTIVE",
                  permissions=",".join(body.permissions))
    db.add(agent)
    db.commit()
    db.refresh(agent)
    raw_key = "ag_" + secrets.token_hex(24)
    cred = AgentCredential(agent_id=agent.id, key_hash=AgentCredential.hash_key(raw_key), key_prefix=raw_key[:10])
    db.add(cred)
    db.commit()
    return {"id": agent.id, "name": agent.name, "api_key": raw_key,
            "note": "Store this API key now -- it will not be shown again."}


@app.patch("/agents/{agent_id}")
def update_agent(agent_id: str, body: AgentUpdate, db: Session = Depends(get_db)):
    a = db.query(Agent).filter(Agent.id == agent_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="agent not found")
    if body.status:
        a.status = body.status
        audit.record_event(db, event_type="agent_status_changed", agent_id=a.id, agent_name=a.name,
                            message=f"Agent status changed to {body.status}")
        manager.broadcast_sync({"event_type": "agent_status_changed", "agent_id": a.id, "agent_name": a.name,
                                 "status": body.status, "timestamp": dt.datetime.utcnow().isoformat()})
    if body.permissions is not None:
        a.permissions = ",".join(body.permissions)
    if body.description is not None:
        a.description = body.description
    db.commit()
    return {"id": a.id, "name": a.name, "status": a.status, "permissions": a.permission_list()}


# ---------------------------------------------------------------------------
# POLICIES
# ---------------------------------------------------------------------------
@app.get("/policies")
def list_policies(db: Session = Depends(get_db)):
    rows = db.query(Policy).order_by(Policy.priority.asc()).all()
    return [
        {"id": p.id, "name": p.name, "description": p.description, "enabled": p.enabled, "priority": p.priority,
         "agent_id": p.agent_id, "tool": p.tool, "effect": p.effect, "condition_field": p.condition_field,
         "condition_operator": p.condition_operator, "condition_value": p.condition_value,
         "created_at": p.created_at}
        for p in rows
    ]


@app.post("/policies")
def create_policy(body: PolicyCreate, db: Session = Depends(get_db)):
    if body.effect not in ("ALLOW", "DENY", "REQUIRE_APPROVAL"):
        raise HTTPException(status_code=400, detail="effect must be ALLOW, DENY, or REQUIRE_APPROVAL")
    p = Policy(**body.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    audit.record_event(db, event_type="policy_created", message=f"Policy '{p.name}' created ({p.effect} on {p.tool})")
    manager.broadcast_sync({"event_type": "policy_created", "policy_id": p.id, "name": p.name, "tool": p.tool,
                            "timestamp": dt.datetime.utcnow().isoformat()})
    return {"id": p.id, "name": p.name}


@app.patch("/policies/{policy_id}")
def update_policy(policy_id: str, body: PolicyUpdate, db: Session = Depends(get_db)):
    p = db.query(Policy).filter(Policy.id == policy_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="policy not found")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    audit.record_event(db, event_type="policy_updated", message=f"Policy '{p.name}' updated (enabled={p.enabled})")
    manager.broadcast_sync({"event_type": "policy_updated", "policy_id": p.id, "name": p.name, "enabled": p.enabled,
                            "timestamp": dt.datetime.utcnow().isoformat()})
    return {"id": p.id, "enabled": p.enabled}


@app.delete("/policies/{policy_id}")
def delete_policy(policy_id: str, db: Session = Depends(get_db)):
    p = db.query(Policy).filter(Policy.id == policy_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="policy not found")
    name = p.name
    db.delete(p)
    db.commit()
    audit.record_event(db, event_type="policy_deleted", message=f"Policy '{name}' deleted")
    manager.broadcast_sync({"event_type": "policy_deleted", "policy_id": policy_id, "name": name,
                            "timestamp": dt.datetime.utcnow().isoformat()})
    return {"deleted": True, "id": policy_id, "name": name}


@app.post("/policies/simulate")
def simulate_policy(body: PolicySimulateRequest, db: Session = Depends(get_db)):
    """Evaluate a hypothetical action WITHOUT executing the tool or writing an ActionRequest."""
    agent = db.query(Agent).filter((Agent.id == body.agent_id) | (Agent.name == body.agent_id)).first()
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    kswitch = gateway.kill_switch_active(db)
    decision = policy_engine.evaluate(db, agent, body.tool, body.arguments, kswitch)
    score, level, factors = risk_engine.assess_risk(body.tool, body.arguments, len(agent.permission_list()))
    return {
        "agent": agent.name, "tool": body.tool, "arguments": body.arguments,
        "permission_check": {"permission": decision.permission, "granted": decision.permission_granted},
        "policy_evaluations": decision.evaluations,
        "risk": {"score": score, "level": level, "factors": factors},
        "decision": decision.decision, "reason": decision.reason,
        "note": "Simulation only -- no tool was executed and no ActionRequest was recorded.",
    }


def _deterministic_policy_proposal(instruction: str, agent_name: Optional[str]) -> dict:
    """Rule-based fallback: always available, needs no API key."""
    import re
    text = instruction.lower()
    amount_match = re.search(r"₹?\s?([\d,]+)", text)
    amount = float(amount_match.group(1).replace(",", "")) if amount_match else 5000
    tool = "refund_customer" if "refund" in text else ("deploy_production" if "deploy" in text else "create_payment")
    return {
        "proposed_policies": [
            {"name": f"Auto-generated: {tool} autolimit", "tool": tool, "effect": "ALLOW",
             "condition_field": "amount", "condition_operator": "lte", "condition_value": amount},
            {"name": f"Auto-generated: {tool} approval above limit", "tool": tool, "effect": "REQUIRE_APPROVAL",
             "condition_field": "amount", "condition_operator": "gt", "condition_value": amount},
        ],
        "agent_name": agent_name,
        "generated_by": "deterministic",
        "note": "This is only a proposal. Nothing has been saved. Review and POST to /policies to activate.",
    }


_POLICY_ASSIST_PROMPT = """You turn a plain-English access-control instruction into a JSON policy \
proposal for AgentGuard, an AI-agent permission gateway.

Respond with ONLY a JSON object of this exact shape (no prose, no markdown fences):
{{
  "proposed_policies": [
    {{"name": "<short name>", "tool": "<tool_name>", "effect": "ALLOW|DENY|REQUIRE_APPROVAL",
      "condition_field": "amount", "condition_operator": "lte|gt|gte|lt|eq|always", "condition_value": <number or null>}}
  ]
}}

Valid tool names: get_customer, refund_customer, export_customers, create_support_ticket, \
deploy_production, create_payment, read_invoice, delete_customer, modify_security_settings.

Instruction: {instruction}"""


def _llm_policy_proposal(instruction: str, agent_name: Optional[str], model: Optional[str] = None) -> dict:
    """Try Featherless AI, then Anthropic, to turn instruction into JSON policy proposal."""
    import json as _json
    prompt = _POLICY_ASSIST_PROMPT.format(instruction=instruction)

    # 1. Featherless AI (preferred open-weight inference)
    if llm_providers.featherless_available():
        try:
            parsed = llm_providers.featherless_json_completion(prompt, model=model)
            parsed["agent_name"] = agent_name
            parsed["generated_by"] = "featherless"
            parsed["model"] = model or llm_providers.get_featherless_model()
            parsed["note"] = "LLM-assisted proposal only. Review and POST to /policies to activate."
            return parsed
        except Exception:
            pass

    # 2. Anthropic fallback
    if llm_providers.anthropic_available():
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            resp = client.messages.create(
                model="claude-sonnet-4-5", max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text").strip().strip("`")
            parsed = _json.loads(text)
            parsed["agent_name"] = agent_name
            parsed["generated_by"] = "anthropic"
            parsed["model"] = "claude-sonnet-4-5"
            parsed["note"] = "LLM-assisted proposal only. Review and POST to /policies to activate."
            return parsed
        except Exception:
            pass

    raise RuntimeError("no LLM provider configured")


@app.post("/policies/assist")
def policy_assist(body: PolicyAssistRequest):
    """
    Optional LLM policy assistant: converts a plain-English instruction into a
    STRUCTURED POLICY PROPOSAL. It never activates a policy by itself --
    the administrator must POST /policies to actually save/activate it.
    """
    try:
        return _llm_policy_proposal(body.instruction, body.agent_name, model=body.model)
    except Exception:
        return _deterministic_policy_proposal(body.instruction, body.agent_name)


# ---------------------------------------------------------------------------
# FEATHERLESS AI & LLM CONFIGURATION ENDPOINTS
# ---------------------------------------------------------------------------
@app.get("/llm/models")
def get_llm_models():
    """List curated Featherless models and current active selection."""
    return {
        "active_model": llm_providers.get_featherless_model(),
        "active_provider": os.getenv("LLM_PROVIDER", "auto"),
        "base_url": llm_providers.get_featherless_base_url(),
        "is_featherless_configured": llm_providers.featherless_available(),
        "models": llm_providers.list_curated_models(),
    }


@app.get("/llm/config")
def get_llm_config():
    """Get active LLM provider configuration (key masked)."""
    key = llm_providers.get_featherless_api_key()
    masked_key = f"{key[:7]}...{key[-4:]}" if (key and len(key) > 12) else ("set" if key else "")
    return {
        "featherless_configured": bool(key),
        "featherless_api_key_masked": masked_key,
        "featherless_model": llm_providers.get_featherless_model(),
        "featherless_base_url": llm_providers.get_featherless_base_url(),
        "llm_provider": os.getenv("LLM_PROVIDER", "auto"),
        "anthropic_configured": llm_providers.anthropic_available(),
    }


@app.post("/llm/config")
def update_llm_config(body: LLMConfigRequest, db: Session = Depends(get_db)):
    """Update Featherless API key, active model, base URL or provider."""
    def _set_setting(key: str, val: str):
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            row = SystemSetting(key=key, value=val)
            db.add(row)
        else:
            row.value = val
        db.commit()

    if body.api_key is not None:
        os.environ["FEATHERLESS_API_KEY"] = body.api_key.strip()
        _set_setting("FEATHERLESS_API_KEY", body.api_key.strip())

    if body.model is not None:
        os.environ["FEATHERLESS_MODEL"] = body.model.strip()
        _set_setting("FEATHERLESS_MODEL", body.model.strip())

    if body.base_url is not None:
        os.environ["FEATHERLESS_BASE_URL"] = body.base_url.strip()
        _set_setting("FEATHERLESS_BASE_URL", body.base_url.strip())

    if body.provider is not None:
        os.environ["LLM_PROVIDER"] = body.provider.strip()
        _set_setting("LLM_PROVIDER", body.provider.strip())

    manager.broadcast_sync({
        "event_type": "llm_config_updated",
        "active_model": llm_providers.get_featherless_model(),
        "timestamp": dt.datetime.utcnow().isoformat(),
    })
    return {"status": "updated", "config": get_llm_config()}


@app.post("/llm/test")
def test_llm_connection(body: LLMTestRequest):
    """Test connection to Featherless AI."""
    res = llm_providers.test_featherless_connection(api_key=body.api_key, base_url=body.base_url)
    return res


@app.post("/actions/{action_request_id}/analyze")
def analyze_action_threat(action_request_id: str, db: Session = Depends(get_db)):
    """Use Featherless AI to generate an AI Security Threat Breakdown for an action."""
    action = db.query(ActionRequest).filter(ActionRequest.id == action_request_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    agent = db.query(Agent).filter(Agent.id == action.agent_id).first()
    context = json.loads(action.context_json or "{}")
    instruction = context.get("reason", "")
    analysis = llm_providers.featherless_security_analysis(
        tool=action.tool,
        arguments=action.arguments(),
        instruction=instruction,
        agent_name=agent.name if agent else "UnknownAgent",
    )
    return {
        "action_id": action.id,
        "tool": action.tool,
        "decision": action.decision,
        "analysis": analysis,
    }


@app.post("/security/analyze-prompt")
def analyze_prompt(body: SecurityAnalysisRequest):
    """Test an action prompt against Featherless security reasoning."""
    analysis = llm_providers.featherless_security_analysis(
        tool=body.tool,
        arguments=body.arguments,
        instruction=body.instruction or "",
        agent_name=body.agent_name or "",
        model=body.model,
    )
    return analysis


# ---------------------------------------------------------------------------
# APPROVALS
# ---------------------------------------------------------------------------
@app.get("/approvals")
def list_approvals(status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(ApprovalRequest)
    if status:
        q = q.filter(ApprovalRequest.status == status)
    rows = q.order_by(ApprovalRequest.requested_at.desc()).all()
    return [
        {"id": r.id, "action_request_id": r.action_request_id, "requested_by_agent": r.requested_by_agent,
         "action": r.action, "arguments": json.loads(r.arguments_json or "{}"), "reason": r.reason,
         "risk_score": r.risk_score, "risk_level": r.risk_level, "status": r.status,
         "requested_at": r.requested_at, "approved_by": r.approved_by, "approved_at": r.approved_at}
        for r in rows
    ]


@app.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, body: ApprovalDecision, db: Session = Depends(get_db)):
    try:
        return gateway.approve_action(db, approval_id, body.approved_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/approvals/{approval_id}/deny")
def deny(approval_id: str, body: ApprovalDecision, db: Session = Depends(get_db)):
    try:
        return gateway.deny_action(db, approval_id, body.reason or "", body.approved_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# AUDIT
# ---------------------------------------------------------------------------
@app.get("/audit-events")
def list_audit_events(limit: int = 200, db: Session = Depends(get_db)):
    rows = db.query(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit).all()
    return [
        {"id": e.id, "request_id": e.request_id, "event_type": e.event_type, "agent_id": e.agent_id,
         "agent_name": e.agent_name, "tool": e.tool, "decision": e.decision, "risk_score": e.risk_score,
         "risk_level": e.risk_level, "policies_triggered": e.policies_triggered,
         "execution_status": e.execution_status, "approver": e.approver, "message": e.message,
         "current_event_hash": e.current_event_hash, "created_at": e.created_at}
        for e in rows
    ]


@app.get("/audit-events/verify")
def verify_audit_chain(db: Session = Depends(get_db)):
    ok, message = audit.verify_chain(db)
    return {"verified": ok, "message": message}


# ---------------------------------------------------------------------------
# SYSTEM
# ---------------------------------------------------------------------------
@app.get("/system/status")
def system_status(db: Session = Depends(get_db)):
    kswitch = gateway.kill_switch_active(db)
    ok, verify_msg = audit.verify_chain(db)
    total_allowed = db.query(ActionRequest).filter(ActionRequest.decision == "ALLOW").count()
    total_blocked = db.query(ActionRequest).filter(ActionRequest.decision == "DENY").count()
    total_pending = db.query(ApprovalRequest).filter(ApprovalRequest.status == "PENDING").count()
    total_actions = db.query(ActionRequest).count()

    return {
        "gateway": "operational", "policy_engine": "operational", "audit_logging": "operational",
        "approval_system": "operational", "kill_switch_active": kswitch,
        "agents": db.query(Agent).count(), "policies": db.query(Policy).count(),
        "actions_processed": total_actions,
        "total_allowed": total_allowed,
        "total_blocked": total_blocked,
        "total_pending": total_pending,
        "audit_chain_verified": ok,
        "audit_chain_message": verify_msg,
        "llm_providers_configured": llm_providers.configured_providers(),
        "featherless_model": llm_providers.get_featherless_model(),
        "featherless_configured": llm_providers.featherless_available(),
    }


@app.post("/system/kill-switch")
def set_kill_switch(enabled: bool, db: Session = Depends(get_db)):
    row = db.query(SystemSetting).filter(SystemSetting.key == "kill_switch").first()
    if not row:
        row = SystemSetting(key="kill_switch", value="off")
        db.add(row)
    row.value = "on" if enabled else "off"
    db.commit()
    audit.record_event(db, event_type="kill_switch_changed", message=f"Kill switch set to {'ON' if enabled else 'OFF'}")
    manager.broadcast_sync({"event_type": "kill_switch_changed", "enabled": enabled,
                             "timestamp": dt.datetime.utcnow().isoformat()})
    return {"kill_switch_active": enabled}


@app.get("/tools")
def list_tools(db: Session = Depends(get_db)):
    rows = db.query(Tool).all()
    return [{"name": t.name, "resource_action": t.resource_action,
             "data_classification": t.data_classification, "destructive": t.destructive} for t in rows]


# ---------------------------------------------------------------------------
# DEMO MODE -- runs real scenarios against the real backend (no fake UI events)
# ---------------------------------------------------------------------------
@app.post("/demo/run/{scenario}")
def run_demo_scenario(scenario: str, db: Session = Depends(get_db)):
    from demo import SCENARIOS
    fn = SCENARIOS.get(scenario)
    if not fn:
        raise HTTPException(status_code=404, detail=f"unknown scenario '{scenario}'. Options: {list(SCENARIOS.keys())}")
    return fn(db)


# ---------------------------------------------------------------------------
# WEBSOCKET -- live security feed
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# FRONTEND (static dashboard)
# ---------------------------------------------------------------------------
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    def serve_dashboard():
        return FileResponse(str(FRONTEND_DIR / "index.html"))
