"""
The AgentGuard Gateway -- the single choke point every protected action
must pass through.

Pipeline (matches spec section 11):
  1. Authenticate agent            (done by caller, via auth.py)
  2. Validate request              (Pydantic, done by caller)
  3. Identify action                -> tool
  4/5. Policy engine (permission + DENY + REQUIRE_APPROVAL + ALLOW)
  6. Risk engine
  7. Determine approval requirement
  8. Produce final decision
  9. Log decision (AuditEvent, PolicyEvaluation, RiskAssessment)
 10. Execute ONLY if permitted (or after approval)

This module never calls an LLM to decide ALLOW/DENY/APPROVAL. It is pure,
deterministic Python.
"""
import json
import uuid
import datetime as dt
from sqlalchemy.orm import Session

from models import Agent, ActionRequest, PolicyEvaluation, RiskAssessment, ApprovalRequest, SystemSetting
import policy_engine
import risk_engine
import audit
import tool_executor
from ws_manager import manager


def kill_switch_active(db: Session) -> bool:
    row = db.query(SystemSetting).filter(SystemSetting.key == "kill_switch").first()
    return bool(row and row.value == "on")


def _broadcast(event_type: str, **fields):
    manager.broadcast_sync({"event_type": event_type, "timestamp": dt.datetime.utcnow().isoformat(), **fields})


def process_action(db: Session, agent: Agent, tool: str, arguments: dict, context: dict, user_id: str = "") -> dict:
    pending = db.query(ActionRequest).filter(
        ActionRequest.agent_id == agent.id,
        ActionRequest.tool == tool,
        ActionRequest.arguments_json == json.dumps(arguments),
        ActionRequest.status == "WAITING_APPROVAL",
    ).first()
    if pending:
        approval = db.query(ApprovalRequest).filter(
            ApprovalRequest.action_request_id == pending.id,
            ApprovalRequest.status == "PENDING",
        ).first()
        if approval:
            return {
                "request_id": pending.request_id, "action_request_id": pending.id,
                "decision": pending.decision, "risk_score": approval.risk_score,
                "risk_level": approval.risk_level, "reason": approval.reason,
                "approval_id": approval.id, "result": None,
                "status": "PENDING_APPROVAL", "duplicate": True,
            }

    request_id = f"req_{uuid.uuid4().hex[:10]}"

    action_req = ActionRequest(
        request_id=request_id, agent_id=agent.id, user_id=user_id, tool=tool,
        arguments_json=json.dumps(arguments), context_json=json.dumps(context),
        decision="PENDING", status="RECEIVED",
    )
    db.add(action_req)
    db.commit()
    db.refresh(action_req)

    _broadcast("action_received", request_id=request_id, action_request_id=action_req.id,
               agent_id=agent.id, agent_name=agent.name, tool=tool, arguments=arguments)

    # --- Policy evaluation ---
    kswitch = kill_switch_active(db)
    decision = policy_engine.evaluate(db, agent, tool, arguments, kswitch)

    for ev in decision.evaluations:
        if ev.get("stage") == "policy_match":
            db.add(PolicyEvaluation(
                action_request_id=action_req.id, policy_id=ev["policy_id"], policy_name=ev["policy_name"],
                matched=True, effect=ev["effect"], explanation=ev.get("condition", ""),
            ))
    db.commit()

    # --- Risk evaluation ---
    score, level, factors = risk_engine.assess_risk(tool, arguments, len(agent.permission_list()))
    db.add(RiskAssessment(action_request_id=action_req.id, score=score, level=level,
                           factors_json=json.dumps(factors)))
    db.commit()

    policies_triggered = ",".join(
        ev["policy_name"] for ev in decision.evaluations if ev.get("stage") == "policy_match"
    )

    result = {
        "request_id": request_id, "action_request_id": action_req.id,
        "decision": decision.decision, "risk_score": score, "risk_level": level,
        "reason": decision.reason, "approval_id": None, "result": None, "status": "",
    }

    if decision.decision == "DENY":
        action_req.decision = "DENY"
        action_req.status = "BLOCKED"
        db.commit()
        explanation = audit.explain_block(decision.reason, tool, arguments)
        audit.record_event(
            db, request_id=request_id, event_type="action_blocked", agent_id=agent.id, agent_name=agent.name,
            user_id=user_id, tool=tool, resource=decision.permission, arguments_json=json.dumps(arguments),
            decision="DENY", risk_score=score, risk_level=level, policies_triggered=policies_triggered,
            execution_status="NOT_EXECUTED", message=explanation,
        )
        _broadcast("action_blocked", request_id=request_id, action_request_id=action_req.id,
                   agent_id=agent.id, agent_name=agent.name, tool=tool, risk_score=score, risk_level=level,
                   reason=decision.reason)
        result["status"] = "BLOCKED"
        result["reason"] = explanation
        return result

    if decision.decision == "REQUIRE_APPROVAL":
        action_req.decision = "REQUIRE_APPROVAL"
        action_req.status = "WAITING_APPROVAL"
        db.commit()

        appr = ApprovalRequest(
            action_request_id=action_req.id, requested_by_agent=agent.name, requested_by_user=user_id,
            action=tool, arguments_json=json.dumps(arguments), reason=context.get("reason", ""),
            risk_score=score, risk_level=level, status="PENDING",
        )
        db.add(appr)
        db.commit()
        db.refresh(appr)

        audit.record_event(
            db, request_id=request_id, event_type="approval_requested", agent_id=agent.id, agent_name=agent.name,
            user_id=user_id, tool=tool, resource=decision.permission, arguments_json=json.dumps(arguments),
            decision="REQUIRE_APPROVAL", risk_score=score, risk_level=level, policies_triggered=policies_triggered,
            execution_status="PENDING", message=decision.reason,
        )
        _broadcast("approval_required", request_id=request_id, action_request_id=action_req.id,
                   approval_id=appr.id, agent_id=agent.id, agent_name=agent.name, tool=tool,
                   arguments=arguments, risk_score=score, risk_level=level, reason=decision.reason)

        result["status"] = "PENDING_APPROVAL"
        result["approval_id"] = appr.id
        return result

    # --- ALLOW: actually execute the protected tool ---
    return _execute_and_finalize(db, action_req, agent, tool, arguments, user_id, decision.reason,
                                  score, level, policies_triggered, decision.permission, request_id)


def _execute_and_finalize(db: Session, action_req: ActionRequest, agent: Agent, tool: str, arguments: dict,
                           user_id: str, reason: str, score: int, level: str, policies_triggered: str,
                           permission: str, request_id: str, approver: str = "") -> dict:
    try:
        tool_result = tool_executor.execute_tool(tool, arguments)
        action_req.decision = "ALLOW"
        action_req.status = "EXECUTED"
        action_req.result_json = json.dumps(tool_result, default=str)
        db.commit()

        audit.record_event(
            db, request_id=request_id, event_type="action_executed", agent_id=agent.id, agent_name=agent.name,
            user_id=user_id, tool=tool, resource=permission, arguments_json=json.dumps(arguments),
            decision="ALLOW", risk_score=score, risk_level=level, policies_triggered=policies_triggered,
            execution_status="EXECUTED", approver=approver, result_json=json.dumps(tool_result, default=str),
            message=reason,
        )
        _broadcast("action_executed", request_id=request_id, action_request_id=action_req.id,
                   agent_id=agent.id, agent_name=agent.name, tool=tool, risk_score=score, risk_level=level,
                   result=tool_result, approver=approver, user_id=user_id)

        return {
            "request_id": request_id, "action_request_id": action_req.id, "decision": "ALLOW",
            "risk_score": score, "risk_level": level, "reason": reason, "approval_id": None,
            "result": tool_result, "status": "EXECUTED",
        }
    except Exception as e:  # fail closed on any executor error
        action_req.decision = "DENY"
        action_req.status = "FAILED"
        db.commit()
        audit.record_event(
            db, request_id=request_id, event_type="execution_error", agent_id=agent.id, agent_name=agent.name,
            user_id=user_id, tool=tool, resource=permission, arguments_json=json.dumps(arguments),
            decision="DENY", risk_score=score, risk_level=level, policies_triggered=policies_triggered,
            execution_status="FAILED", message=f"Execution error, failing closed: {e}",
        )
        _broadcast("execution_error", request_id=request_id, action_request_id=action_req.id,
                   agent_id=agent.id, agent_name=agent.name, tool=tool, error=str(e))
        return {
            "request_id": request_id, "action_request_id": action_req.id, "decision": "DENY",
            "risk_score": score, "risk_level": level, "reason": f"Execution failed, action denied: {e}",
            "approval_id": None, "result": None, "status": "FAILED",
        }


def approve_action(db: Session, approval_id: str, approved_by: str) -> dict:
    appr = db.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).first()
    if not appr:
        raise ValueError("approval request not found")
    if appr.status != "PENDING":
        raise ValueError(f"approval request already {appr.status}")

    action_req = db.query(ActionRequest).filter(ActionRequest.id == appr.action_request_id).first()
    agent = db.query(Agent).filter(Agent.id == action_req.agent_id).first()
    arguments = json.loads(appr.arguments_json or "{}")

    # Re-check authorization at approval time (agent could have been disabled meanwhile,
    # or the kill switch flipped on) -- approval does not bypass the gateway.
    if kill_switch_active(db) or agent.status != "ACTIVE":
        appr.status = "DENIED"
        appr.rejection_reason = "Re-authorization failed at approval time (kill switch or disabled agent)."
        db.commit()
        action_req.decision = "DENY"
        action_req.status = "BLOCKED"
        db.commit()
        audit.record_event(
            db, request_id=action_req.request_id, event_type="approval_auto_denied", agent_id=agent.id,
            agent_name=agent.name, tool=action_req.tool, decision="DENY",
            message="Approval could not proceed: re-authorization failed.",
        )
        _broadcast("approval_denied", approval_id=approval_id, action_request_id=action_req.id,
                   reason="Re-authorization failed at approval time")
        raise ValueError("Re-authorization failed: kill switch active or agent disabled")

    appr.status = "APPROVED"
    appr.approved_by = approved_by
    appr.approved_at = dt.datetime.utcnow()
    db.commit()

    audit.record_event(
        db, request_id=action_req.request_id, event_type="approval_granted", agent_id=agent.id,
        agent_name=agent.name, tool=action_req.tool, decision="REQUIRE_APPROVAL",
        risk_score=appr.risk_score, risk_level=appr.risk_level, approver=approved_by,
        message=f"Approved by {approved_by}",
    )
    _broadcast("approval_granted", approval_id=approval_id, action_request_id=action_req.id,
               approver=approved_by, tool=action_req.tool)

    result = _execute_and_finalize(
        db, action_req, agent, action_req.tool, arguments, action_req.user_id,
        f"Approved by {approved_by}", appr.risk_score, appr.risk_level, "", "", action_req.request_id,
        approver=approved_by,
    )
    result["approval_id"] = approval_id
    return result


def deny_action(db: Session, approval_id: str, reason: str, denied_by: str) -> dict:
    appr = db.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).first()
    if not appr:
        raise ValueError("approval request not found")
    if appr.status != "PENDING":
        raise ValueError(f"approval request already {appr.status}")

    action_req = db.query(ActionRequest).filter(ActionRequest.id == appr.action_request_id).first()
    agent = db.query(Agent).filter(Agent.id == action_req.agent_id).first()

    appr.status = "DENIED"
    appr.rejection_reason = reason or "Denied by administrator"
    appr.approved_by = denied_by
    appr.approved_at = dt.datetime.utcnow()
    db.commit()

    action_req.decision = "DENY"
    action_req.status = "DENIED"
    db.commit()

    audit.record_event(
        db, request_id=action_req.request_id, event_type="approval_denied", agent_id=agent.id,
        agent_name=agent.name, tool=action_req.tool, decision="DENY",
        risk_score=appr.risk_score, risk_level=appr.risk_level, approver=denied_by,
        execution_status="NOT_EXECUTED", message=f"Denied by {denied_by}: {appr.rejection_reason}",
    )
    _broadcast("approval_denied", approval_id=approval_id, action_request_id=action_req.id,
               tool=action_req.tool, reason=appr.rejection_reason)

    return {
        "approval_id": approval_id, "action_request_id": action_req.id, "decision": "DENY",
        "status": "DENIED_NOT_EXECUTED", "reason": appr.rejection_reason,
    }
