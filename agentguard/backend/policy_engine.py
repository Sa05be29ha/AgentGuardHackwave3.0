"""
Deterministic policy engine.

Evaluation order (matches the spec):
  1. Global kill switch
  2. Agent active/disabled
  3. Agent permission check (resource.action must be granted)
  4. Explicit DENY policies for this tool (agent-specific first, then global)
  5. REQUIRE_APPROVAL policies whose condition matches
  6. ALLOW policies whose condition matches
  7. Default: DENY (fail closed -- nothing explicitly allowed this)

A matching DENY always wins over ALLOW, regardless of priority ordering.
No LLM is involved anywhere in this module.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session

from models import Agent, Policy


TOOL_TO_PERMISSION = {
    "get_customer": "customer.read",
    "get_customer_orders": "order.read",
    "update_customer": "customer.update",
    "export_customers": "customer.export",
    "delete_customer": "customer.delete",
    "create_support_ticket": "ticket.create",
    "send_customer_email": "communication.send",
    "send_message": "communication.send",
    "refund_customer": "refund.create",
    "get_invoice": "invoice.read",
    "create_payment": "payment.create",
    "get_payment": "payment.read",
    "export_financial_records": "financial.export",
    "modify_accounting_configuration": "accounting.modify",
    "read_repository": "repository.read",
    "create_branch": "repository.write",
    "create_pull_request": "repository.write",
    "deploy_production": "deployment.create",
    "delete_production_database": "database.delete",
    "disable_security_controls": "security.modify",
    "modify_security_settings": "security.modify",
}


@dataclass
class PolicyDecision:
    decision: str  # ALLOW / DENY / REQUIRE_APPROVAL
    reason: str
    matched_policy: Optional[Policy] = None
    permission: str = ""
    permission_granted: bool = False
    evaluations: List[Dict[str, Any]] = field(default_factory=list)


def _condition_matches(policy: Policy, arguments: Dict[str, Any]) -> bool:
    if policy.condition_operator == "always" or policy.condition_field == "always":
        return True
    value = arguments.get(policy.condition_field)
    if value is None:
        return False
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    cv = policy.condition_value
    if cv is None:
        return False
    ops = {
        "gt": value > cv, "gte": value >= cv,
        "lt": value < cv, "lte": value <= cv,
        "eq": value == cv,
    }
    return ops.get(policy.condition_operator, False)


def evaluate(db: Session, agent: Agent, tool: str, arguments: Dict[str, Any], kill_switch_on: bool) -> PolicyDecision:
    evaluations: List[Dict[str, Any]] = []

    if kill_switch_on:
        return PolicyDecision(
            decision="DENY",
            reason="Global emergency kill switch is ACTIVE -- all autonomous agent actions are blocked.",
            evaluations=evaluations,
        )

    if agent.status != "ACTIVE":
        return PolicyDecision(
            decision="DENY",
            reason=f"Agent '{agent.name}' is {agent.status}, so its requests are rejected.",
            evaluations=evaluations,
        )

    permission = TOOL_TO_PERMISSION.get(tool, tool)
    permission_granted = agent.has_permission(permission)
    evaluations.append({
        "stage": "permission_check", "permission": permission,
        "granted": permission_granted,
    })

    policies = (
        db.query(Policy)
        .filter(Policy.tool == tool, Policy.enabled == True)  # noqa: E712
        .filter((Policy.agent_id == agent.id) | (Policy.agent_id.is_(None)))
        .order_by(Policy.priority.asc())
        .all()
    )

    matching = [p for p in policies if _condition_matches(p, arguments)]
    for p in matching:
        evaluations.append({
            "stage": "policy_match", "policy_id": p.id, "policy_name": p.name,
            "effect": p.effect, "condition": f"{p.condition_field} {p.condition_operator} {p.condition_value}",
        })

    # DENY always wins
    deny_policy = next((p for p in matching if p.effect == "DENY"), None)
    if deny_policy:
        return PolicyDecision(
            decision="DENY",
            reason=f'BLOCKED by policy "{deny_policy.name}": {deny_policy.description}',
            matched_policy=deny_policy, permission=permission,
            permission_granted=permission_granted, evaluations=evaluations,
        )

    if not permission_granted:
        return PolicyDecision(
            decision="DENY",
            reason=f"Agent '{agent.name}' does not have permission '{permission}'. "
                   f"No policy explicitly grants this action, so it fails closed.",
            permission=permission, permission_granted=False, evaluations=evaluations,
        )

    approval_policy = next((p for p in matching if p.effect == "REQUIRE_APPROVAL"), None)
    if approval_policy:
        return PolicyDecision(
            decision="REQUIRE_APPROVAL",
            reason=f'Policy "{approval_policy.name}" requires human approval: {approval_policy.description}',
            matched_policy=approval_policy, permission=permission,
            permission_granted=True, evaluations=evaluations,
        )

    allow_policy = next((p for p in matching if p.effect == "ALLOW"), None)
    if allow_policy:
        return PolicyDecision(
            decision="ALLOW",
            reason=f'Allowed by policy "{allow_policy.name}".',
            matched_policy=allow_policy, permission=permission,
            permission_granted=True, evaluations=evaluations,
        )

    # Permission granted, no specific policy overrides -- allow by default.
    return PolicyDecision(
        decision="ALLOW",
        reason=f"Agent has permission '{permission}' and no policy restricts this request.",
        permission=permission, permission_granted=True, evaluations=evaluations,
    )
