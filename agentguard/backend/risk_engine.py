"""
Deterministic risk scoring.

This NEVER uses an LLM. Given a tool name and arguments, it produces a
0-100 score from fixed, explainable factors. The same input always produces
the same score.
"""
from typing import Dict, Any, List, Tuple

# Base sensitivity per tool (0-100)
TOOL_BASE_RISK = {
    "get_customer": 5,
    "update_customer": 15,
    "create_support_ticket": 5,
    "send_customer_email": 10,
    "send_message": 5,
    "refund_customer": 20,
    "get_invoice": 5,
    "create_payment": 25,
    "get_payment": 5,
    "read_repository": 5,
    "create_branch": 10,
    "create_pull_request": 10,
    "deploy_production": 55,
    "export_customers": 70,
    "delete_customer": 75,
    "modify_security_settings": 85,
    "export_financial_records": 80,
    "modify_accounting_configuration": 75,
    "delete_production_database": 95,
    "disable_security_controls": 95,
}

DESTRUCTIVE_TOOLS = {"delete_customer", "delete_production_database", "disable_security_controls"}
BULK_TOOLS = {"export_customers", "export_financial_records"}


def _level(score: int) -> str:
    if score >= 80:
        return "CRITICAL"
    if score >= 60:
        return "HIGH"
    if score >= 30:
        return "MEDIUM"
    return "LOW"


def assess_risk(tool: str, arguments: Dict[str, Any], agent_permission_count: int = 0) -> Tuple[int, str, List[Dict[str, Any]]]:
    factors: List[Dict[str, Any]] = []
    score = TOOL_BASE_RISK.get(tool, 20)
    factors.append({"factor": "Base action sensitivity", "points": score})

    amount = arguments.get("amount")
    if isinstance(amount, (int, float)):
        if amount > 50000:
            pts = 35
        elif amount > 5000:
            pts = 25
        elif amount > 1000:
            pts = 10
        else:
            pts = 0
        if pts:
            score += pts
            factors.append({"factor": "Financial amount threshold", "points": pts})

    if tool in DESTRUCTIVE_TOOLS:
        score += 30
        factors.append({"factor": "Destructive action", "points": 30})

    if str(arguments.get("environment", "")).lower() == "production" or tool == "deploy_production":
        score += 20
        factors.append({"factor": "Production environment", "points": 20})

    limit = arguments.get("limit")
    if tool in BULK_TOOLS or (isinstance(limit, (int, float)) and limit > 100):
        score += 25
        factors.append({"factor": "Bulk operation", "points": 25})

    if arguments.get("external_recipient") or tool == "send_customer_email":
        pts = 5
        score += pts
        factors.append({"factor": "External recipient", "points": pts})

    score = max(0, min(100, score))
    return score, _level(score), factors
