"""
Core end-to-end tests (spec section 48/49) proving the authorization
pipeline actually enforces decisions -- not merely returns a status code.
"""
from models import Agent, ApprovalRequest
from mock_services import customer_db, payment_service, git_service
import gateway


def test_agent_authentication_valid_key(client, seeded_data):
    resp = client.get("/system/status")
    assert resp.status_code == 200
    resp = client.post("/gateway/action",
                        headers={"Authorization": f"Bearer {seeded_data['customer_support_key']}"},
                        json={"agent_id": "CustomerSupportAgent", "tool": "get_customer",
                              "arguments": {"customer_id": "cust-4821"}})
    assert resp.status_code == 200
    assert resp.json()["decision"] == "ALLOW"


def test_agent_authentication_invalid_key(client):
    resp = client.post("/gateway/action", headers={"Authorization": "Bearer not-a-real-key"},
                        json={"agent_id": "x", "tool": "get_customer", "arguments": {"customer_id": "cust-4821"}})
    assert resp.status_code == 401


def test_missing_auth_header_rejected(client):
    resp = client.post("/gateway/action", json={"agent_id": "x", "tool": "get_customer", "arguments": {}})
    assert resp.status_code == 401


def test_allowed_action_executes_and_mutates_state(db_session):
    agent = db_session.query(Agent).filter(Agent.name == "CustomerSupportAgent").first()
    before = len(payment_service.all_transactions())
    result = gateway.process_action(db_session, agent, "refund_customer",
                                     {"customer_id": "cust-4821", "amount": 3000, "currency": "INR"}, {})
    assert result["decision"] == "ALLOW"
    assert result["status"] == "EXECUTED"
    after = len(payment_service.all_transactions())
    assert after == before + 1, "refund must actually mutate transaction state"


def test_amount_based_policy_triggers_approval(db_session):
    agent = db_session.query(Agent).filter(Agent.name == "CustomerSupportAgent").first()
    before = len(payment_service.all_transactions())
    result = gateway.process_action(db_session, agent, "refund_customer",
                                     {"customer_id": "cust-4821", "amount": 50000, "currency": "INR"}, {})
    assert result["decision"] == "REQUIRE_APPROVAL"
    assert result["status"] == "PENDING_APPROVAL"
    after = len(payment_service.all_transactions())
    assert after == before, "refund must NOT execute before approval"
    return result["approval_id"]


def test_approval_then_execution(db_session):
    agent = db_session.query(Agent).filter(Agent.name == "CustomerSupportAgent").first()
    before = len(payment_service.all_transactions())
    pending = gateway.process_action(db_session, agent, "refund_customer",
                                      {"customer_id": "cust-4821", "amount": 60000, "currency": "INR"}, {})
    assert pending["status"] == "PENDING_APPROVAL"
    assert len(payment_service.all_transactions()) == before

    outcome = gateway.approve_action(db_session, pending["approval_id"], "admin")
    assert outcome["decision"] == "ALLOW"
    assert outcome["status"] == "EXECUTED"
    assert len(payment_service.all_transactions()) == before + 1, "refund must execute exactly once, after approval"


def test_approval_denial_never_executes(db_session):
    agent = db_session.query(Agent).filter(Agent.name == "CustomerSupportAgent").first()
    before = len(payment_service.all_transactions())
    pending = gateway.process_action(db_session, agent, "refund_customer",
                                      {"customer_id": "cust-4821", "amount": 70000, "currency": "INR"}, {})
    outcome = gateway.deny_action(db_session, pending["approval_id"], "not legitimate", "admin")
    assert outcome["decision"] == "DENY"
    assert len(payment_service.all_transactions()) == before, "denied refund must never execute"


def test_unauthorized_export_is_blocked_and_db_untouched(db_session):
    """This is one of the most important tests in the project (spec #49)."""
    agent = db_session.query(Agent).filter(Agent.name == "CustomerSupportAgent").first()
    result = gateway.process_action(db_session, agent, "export_customers", {"limit": 10000}, {})
    assert result["decision"] == "DENY"
    assert result["status"] == "BLOCKED"
    # Prove the protected service itself was never actually called with effect:
    # export_customers() is a pure read of static data, so we assert the gateway
    # never reached tool_executor by checking the ActionRequest has no result stored.
    from models import ActionRequest
    row = db_session.query(ActionRequest).filter(ActionRequest.id == result["action_request_id"]).first()
    assert row.result_json in ("{}", None)


def test_production_deployment_requires_approval_and_denial_leaves_state_unchanged(db_session):
    agent = db_session.query(Agent).filter(Agent.name == "DevOpsAgent").first()
    before_version = git_service.deployment_state()["current_version"]
    pending = gateway.process_action(db_session, agent, "deploy_production",
                                      {"version": "v9.9.9", "environment": "production"}, {})
    assert pending["status"] == "PENDING_APPROVAL"
    gateway.deny_action(db_session, pending["approval_id"], "not approved this cycle", "admin")
    assert git_service.deployment_state()["current_version"] == before_version


def test_production_db_deletion_always_blocked(db_session):
    agent = db_session.query(Agent).filter(Agent.name == "DevOpsAgent").first()
    result = gateway.process_action(db_session, agent, "delete_production_database", {}, {})
    assert result["decision"] == "DENY"


def test_kill_switch_blocks_everything(db_session):
    from models import SystemSetting
    agent = db_session.query(Agent).filter(Agent.name == "CustomerSupportAgent").first()
    row = db_session.query(SystemSetting).filter(SystemSetting.key == "kill_switch").first()
    row.value = "on"
    db_session.commit()
    result = gateway.process_action(db_session, agent, "get_customer", {"customer_id": "cust-4821"}, {})
    assert result["decision"] == "DENY"
    row.value = "off"
    db_session.commit()


def test_disabled_agent_is_blocked(db_session):
    agent = db_session.query(Agent).filter(Agent.name == "CustomerSupportAgent").first()
    agent.status = "DISABLED"
    db_session.commit()
    result = gateway.process_action(db_session, agent, "get_customer", {"customer_id": "cust-4821"}, {})
    assert result["decision"] == "DENY"
    agent.status = "ACTIVE"
    db_session.commit()


def test_fail_closed_on_unknown_tool(db_session):
    agent = db_session.query(Agent).filter(Agent.name == "CustomerSupportAgent").first()
    result = gateway.process_action(db_session, agent, "totally_unknown_tool", {}, {})
    # No permission is granted for an unmapped tool -> DENY (fail closed)
    assert result["decision"] == "DENY"


def test_audit_event_created_for_every_decision(db_session):
    from models import AuditEvent
    before = db_session.query(AuditEvent).count()
    agent = db_session.query(Agent).filter(Agent.name == "CustomerSupportAgent").first()
    gateway.process_action(db_session, agent, "get_customer", {"customer_id": "cust-1001"}, {})
    after = db_session.query(AuditEvent).count()
    assert after > before


def test_audit_chain_integrity(db_session):
    import audit
    # Other tests write audit events through separate Sessions (e.g. via the
    # HTTP TestClient's own request-scoped session). Commit/refresh here so
    # this long-lived fixture session reads their latest committed rows
    # instead of a stale snapshot from its own still-open transaction.
    db_session.commit()
    ok, msg = audit.verify_chain(db_session)
    assert ok is True, msg
