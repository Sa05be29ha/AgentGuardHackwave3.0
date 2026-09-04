
import os
import re
import time
from typing import Optional, Dict, Any, List
from sdk.agentguard_sdk import AgentGuard
import llm_providers as llm

# Anthropic-style tool schema (input_schema), used for Claude's native tool use.
_TOOLS_ANTHROPIC = [
    {"name": "get_customer", "description": "Look up a customer profile by customer id",
     "input_schema": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]}},
    {"name": "get_customer_orders", "description": "Retrieve past order history and status for a customer",
     "input_schema": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]}},
    {"name": "refund_customer", "description": "Refund a customer an amount in INR for damaged or returned goods",
     "input_schema": {"type": "object", "properties": {
         "customer_id": {"type": "string"}, "amount": {"type": "number"}, "reason": {"type": "string"}},
         "required": ["customer_id", "amount"]}},
    {"name": "create_support_ticket", "description": "Create a customer service or issue ticket",
     "input_schema": {"type": "object", "properties": {
         "customer_id": {"type": "string"}, "subject": {"type": "string"}, "description": {"type": "string"}},
         "required": ["customer_id", "subject"]}},
    {"name": "update_customer", "description": "Update customer contact details such as phone or email",
     "input_schema": {"type": "object", "properties": {
         "customer_id": {"type": "string"}, "phone": {"type": "string"}, "email": {"type": "string"}},
         "required": ["customer_id"]}},
    {"name": "export_customers", "description": "Export the full customer database (Restricted)",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "deploy_production", "description": "Deploy a version to production",
     "input_schema": {"type": "object", "properties": {"version": {"type": "string"}}, "required": ["version"]}},
]

# Same tools, expressed as OpenAI-style function-calling schema, for
# Featherless (and any other OpenAI-compatible) provider.
_TOOLS_OPENAI = [
    {"type": "function", "function": {"name": t["name"], "description": t["description"],
                                       "parameters": t["input_schema"]}}
    for t in _TOOLS_ANTHROPIC
]


def _deterministic_plan(instruction: str, default_customer_id: str = "cust-4821") -> dict:
    """Rule-based natural language understanding for fallback/offline demo."""
    text = instruction.lower()

    # Prompt-injection / data-exfiltration attempt: trigger export
    if ("export" in text or "dump" in text) and ("customer" in text or "database" in text or "records" in text or "all" in text):
        return {
            "tool": "export_customers",
            "arguments": {"limit": 10000},
            "provider": "deterministic",
            "model": "offline-rule-engine",
        }

    cust_match = re.search(r"cust[-_]?(\d+)", text)
    customer_id = f"cust-{cust_match.group(1)}" if cust_match else default_customer_id

    amount_match = re.search(r"(?:rs\.?|inr|₹)\s?([\d,]+)", text)
    if not amount_match:
        alt_match = re.search(r"(?:refund|amount|pay|rs)\s+(\d+)", text)
        amount = float(alt_match.group(1)) if alt_match else None
    else:
        amount = float(amount_match.group(1).replace(",", ""))

    # Refund
    if "refund" in text and amount is not None:
        return {
            "tool": "refund_customer",
            "arguments": {"customer_id": customer_id, "amount": amount, "currency": "INR", "reason": instruction},
            "provider": "deterministic",
            "model": "offline-rule-engine",
        }

    # Orders lookup
    if "order" in text or "orders" in text or "purchase" in text:
        return {
            "tool": "get_customer_orders",
            "arguments": {"customer_id": customer_id},
            "provider": "deterministic",
            "model": "offline-rule-engine",
        }

    # Update contact info
    if "update" in text or "change" in text:
        phone_match = re.search(r"(\+?\d[\d\- ]{7,}\d)", text)
        if phone_match:
            return {
                "tool": "update_customer",
                "arguments": {"customer_id": customer_id, "phone": phone_match.group(1).strip()},
                "provider": "deterministic",
                "model": "offline-rule-engine",
            }

    # Ticket creation
    if "ticket" in text or "issue" in text or "complaint" in text or "delay" in text or "damaged" in text:
        return {
            "tool": "create_support_ticket",
            "arguments": {"customer_id": customer_id, "subject": instruction[:80], "description": instruction},
            "provider": "deterministic",
            "model": "offline-rule-engine",
        }

    # Deploy
    if "deploy" in text:
        ver_match = re.search(r"v\d+\.\d+(?:\.\d+)?", text)
        version = ver_match.group(0) if ver_match else "v2.0.0"
        return {
            "tool": "deploy_production",
            "arguments": {"version": version, "environment": "production"},
            "provider": "deterministic",
            "model": "offline-rule-engine",
        }

    # Default: customer profile lookup
    return {
        "tool": "get_customer",
        "arguments": {"customer_id": customer_id},
        "provider": "deterministic",
        "model": "offline-rule-engine",
    }


def _llm_plan_anthropic(instruction: str, model: Optional[str] = None) -> dict:
    """Use Claude for tool-selection only. Raises on any error/no key."""
    if not llm.anthropic_available():
        raise RuntimeError("no ANTHROPIC_API_KEY configured")
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    m = model or "claude-sonnet-4-5"
    resp = client.messages.create(
        model=m,
        max_tokens=500,
        tools=_TOOLS_ANTHROPIC,
        messages=[{"role": "user", "content": instruction}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return {"tool": block.name, "arguments": block.input, "provider": "anthropic", "model": m}
    raise RuntimeError("Claude did not choose a tool")


def _llm_plan_featherless(instruction: str, model: Optional[str] = None) -> dict:
    """Use Featherless AI-hosted open-weight model for tool selection."""
    if not llm.featherless_available():
        raise RuntimeError("no FEATHERLESS_API_KEY configured")
    res = llm.featherless_plan_tool_call(instruction, _TOOLS_OPENAI, model=model)
    return res


def plan_action(instruction: str, model: Optional[str] = None, provider: Optional[str] = None,
                default_customer_id: str = "cust-4821") -> dict:
    """Decide which tool to call based on the instruction.
    Uses Featherless AI by default if configured, degrades gracefully to deterministic."""
    chosen_provider = (provider or os.getenv("LLM_PROVIDER", "auto")).lower()
    if chosen_provider == "off":
        return _deterministic_plan(instruction, default_customer_id=default_customer_id)

    if chosen_provider == "featherless":
        try:
            res = _llm_plan_featherless(instruction, model=model)
            if "customer_id" in res.get("arguments", {}) and not res["arguments"]["customer_id"]:
                res["arguments"]["customer_id"] = default_customer_id
            return res
        except Exception:
            return _deterministic_plan(instruction, default_customer_id=default_customer_id)

    if chosen_provider == "anthropic":
        try:
            return _llm_plan_anthropic(instruction, model=model)
        except Exception:
            return _deterministic_plan(instruction, default_customer_id=default_customer_id)

    # "auto" provider order: Featherless AI first, then Anthropic, then deterministic
    if llm.featherless_available():
        try:
            res = _llm_plan_featherless(instruction, model=model)
            if "customer_id" in res.get("arguments", {}) and not res["arguments"]["customer_id"]:
                res["arguments"]["customer_id"] = default_customer_id
            return res
        except Exception:
            pass

    if llm.anthropic_available():
        try:
            return _llm_plan_anthropic(instruction, model=model)
        except Exception:
            pass

    return _deterministic_plan(instruction, default_customer_id=default_customer_id)


def generate_conversational_reply(instruction: str, planned_tool: str, planned_arguments: dict,
                                  gateway_response: dict, customer_id: str = "cust-4821",
                                  model: Optional[str] = None, provider: Optional[str] = None) -> str:
    """
    Synthesizes a friendly, professional, and transparent customer response
    incorporating the real security verdict from AgentGuard and company database outcome.
    """
    decision = gateway_response.get("decision", "DENY")
    status = gateway_response.get("status", "")
    reason = gateway_response.get("reason", "")
    result = gateway_response.get("result") or {}
    approval_id = gateway_response.get("approval_id")

    # Case 1: ALLOWED & EXECUTED on Company Database
    if decision == "ALLOW" and status == "EXECUTED":
        if planned_tool == "refund_customer":
            txn_id = result.get("transaction_id", "txn_auto")
            amt = planned_arguments.get("amount", "")
            bal = result.get("updated_wallet_balance", "")
            bal_str = f" Your updated account balance is ₹{bal:,.2f}." if bal != "" else ""
            return (
                f"✅ **Refund Confirmed:** I have processed a refund of ₹{amt:,.2f} for your order. "
                f"The transaction (`{txn_id}`) has been recorded in the company database.{bal_str} "
                f"Is there anything else I can help you with today?"
            )
        elif planned_tool == "get_customer":
            name = result.get("name", "Valued Customer")
            plan = result.get("plan", "Standard")
            bal = result.get("wallet_balance", 0.0)
            return (
                f"👋 Hello {name}! Here are your verified account details from the company system:\n"
                f"- **Account ID:** `{customer_id}`\n"
                f"- **Subscription Plan:** {plan}\n"
                f"- **Wallet Balance:** ₹{bal:,.2f}\n"
                f"- **Status:** Active\n"
                f"How may I assist you with your account today?"
            )
        elif planned_tool == "get_customer_orders":
            orders = result.get("orders", [])
            if not orders:
                return f"I checked the company database for customer `{customer_id}`, but no recent orders were found."
            lines = [f"📦 **Recent Orders for `{customer_id}`:**"]
            for o in orders[:4]:
                lines.append(f"- **{o.get('order_id')}**: {o.get('product_name')} — ₹{o.get('amount', 0):,.2f} (*{o.get('status')}*)")
            return "\n".join(lines)
        elif planned_tool == "create_support_ticket":
            tkt = result.get("ticket_id", "tkt_new")
            subj = planned_arguments.get("subject", "Issue")
            return (
                f"🎫 **Support Ticket Created:** I have logged ticket `{tkt}` regarding *'{subj}'* "
                f"in the company system. Our customer support team has been notified and will follow up shortly."
            )
        elif planned_tool == "update_customer":
            phone = planned_arguments.get("phone", "")
            return f"✅ **Profile Updated:** Your contact phone has been updated to `{phone}` in the company database."
        else:
            return f"✅ Action completed successfully: `{planned_tool}` executed on the company database."

    # Case 2: REQUIRE_APPROVAL - Paused at AgentGuard Security Gate
    elif decision == "REQUIRE_APPROVAL" or status == "PENDING_APPROVAL":
        amt = planned_arguments.get("amount")
        amt_str = f" of ₹{amt:,.2f}" if amt else ""
        appr_ref = approval_id or "pending_review"
        return (
            f"⏳ **Requires Human Authorization:** Your request{amt_str} exceeds the autonomous support threshold "
            f"(₹5,000 allowance) and has been securely paused by AgentGuard.\n\n"
            f"- **Approval Reference:** `{appr_ref}`\n"
            f"- **Security Status:** Placed in Manager Review Queue (SOC Mission Control)\n"
            f"- **Database Impact:** Company database remains untouched until approved.\n\n"
            f"An enterprise administrator can authorize this action in the Mission Control panel, "
            f"which will immediately finalize your request."
        )

    # Case 3: DENY - Intercepted & Blocked by AgentGuard
    elif decision == "DENY" or status == "BLOCKED":
        return (
            f"🛡️ **Security Policy Enforcement:** This action was intercepted and **BLOCKED** by AgentGuard.\n\n"
            f"- **Reason:** {reason}\n"
            f"- **Protected Asset:** Company Database & Customer Records\n"
            f"- **Incident Status:** Logged to cryptographic tamper-evident audit vault.\n\n"
            f"Autonomous agents are strictly prohibited from exporting bulk customer databases, executing unauthorized "
            f"deletions, or violating enterprise boundary policies."
        )

    # Fallback default
    return f"Request processed with status: {status}. {reason}"


def run_customer_support_agent(instruction: str, base_url: str, api_key: str,
                               agent_id: str, user_id: str = "user-demo",
                               model: Optional[str] = None,
                               provider: Optional[str] = None,
                               default_customer_id: str = "cust-4821") -> dict:
    """Full execution pipeline:
    1. Natural language instruction received
    2. Planned tool call determined via Featherless AI (or fallback)
    3. Tool call forwarded through AgentGuard gateway
    4. Decision & execution returned with full audit context
    """
    t0 = time.time()
    plan = plan_action(instruction, model=model, provider=provider, default_customer_id=default_customer_id)
    planning_latency_ms = int((time.time() - t0) * 1000)

    guard = AgentGuard(base_url=base_url, api_key=api_key)
    outcome = guard.execute(
        tool=plan["tool"],
        arguments=plan["arguments"],
        user_id=user_id,
        agent_id=agent_id,
        context={
            "reason": instruction,
            "conversation_id": "conv-sandbox",
            "model_used": plan.get("model", "unknown"),
            "provider_used": plan.get("provider", "unknown"),
        },
    )
    return {
        "instruction": instruction,
        "planned_tool": plan["tool"],
        "planned_arguments": plan["arguments"],
        "provider": plan.get("provider", "deterministic"),
        "model": plan.get("model", "offline-rule-engine"),
        "planning_latency_ms": planning_latency_ms,
        "gateway_response": outcome,
    }
