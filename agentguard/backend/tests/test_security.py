def test_protected_tools_have_no_direct_http_route(client, seeded_data):
    """Mock services are only reachable through gateway.process_action -- there
    must be no HTTP route that lets a caller invoke them directly."""
    for path in ("/refund", "/refund_customer", "/mock/refund", "/customers/export", "/export_customers"):
        resp = client.get(path)
        assert resp.status_code == 404
        resp = client.post(path, json={})
        assert resp.status_code == 404


def test_policy_simulator_does_not_execute_or_persist_action(client, seeded_data):
    from models import ActionRequest
    resp = client.post("/policies/simulate", json={
        "agent_id": "CustomerSupportAgent", "tool": "refund_customer",
        "arguments": {"customer_id": "cust-4821", "amount": 25000},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "REQUIRE_APPROVAL"
    assert "Simulation only" in body["note"]


def test_gateway_action_requires_body_agent_match(client, seeded_data):
    resp = client.post("/gateway/action",
                        headers={"Authorization": f"Bearer {seeded_data['customer_support_key']}"},
                        json={"agent_id": "SomeOtherAgent", "tool": "get_customer", "arguments": {"customer_id": "cust-4821"}})
    assert resp.status_code == 403
