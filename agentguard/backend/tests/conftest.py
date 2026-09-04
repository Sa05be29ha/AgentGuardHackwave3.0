import os
import sys
import pathlib

# Use an isolated on-disk sqlite DB for the whole test session, and make sure
# `backend/` is importable the same way it is when running `uvicorn main:app`.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
TEST_DB = pathlib.Path(__file__).resolve().parent / "test_agentguard.db"
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

import pytest
from database import Base, engine, SessionLocal
from models import Agent, AgentCredential, Policy, SystemSetting

Base.metadata.create_all(bind=engine)


@pytest.fixture(scope="session")
def db_session():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(scope="session", autouse=True)
def seeded_data(db_session):
    if db_session.query(Agent).count() == 0:
        cs = Agent(name="CustomerSupportAgent", owner="CX", status="ACTIVE",
                   permissions="customer.read,order.read,ticket.create,communication.send,refund.create")
        devops = Agent(name="DevOpsAgent", owner="Eng", status="ACTIVE",
                        permissions="repository.read,repository.write,deployment.create")
        db_session.add_all([cs, devops])
        db_session.commit()
        db_session.refresh(cs)
        db_session.refresh(devops)

        db_session.add_all([
            Policy(name="Support Refund Autolimit", agent_id=cs.id, tool="refund_customer", effect="ALLOW",
                   condition_field="amount", condition_operator="lte", condition_value=5000, priority=10),
                 Policy(name="Support Order Lookup", agent_id=cs.id, tool="get_customer_orders", effect="ALLOW", priority=10),
            Policy(name="Support Refund Approval", agent_id=cs.id, tool="refund_customer", effect="REQUIRE_APPROVAL",
                   condition_field="amount", condition_operator="gt", condition_value=5000, priority=20),
            Policy(name="Customer Data Export Protection", agent_id=cs.id, tool="export_customers", effect="DENY", priority=1),
            Policy(name="Production Deploy Approval", agent_id=devops.id, tool="deploy_production",
                   effect="REQUIRE_APPROVAL", condition_field="always", condition_operator="always", priority=10),
            Policy(name="No Production DB Deletion", agent_id=devops.id, tool="delete_production_database",
                   effect="DENY", priority=1),
        ])
        db_session.add(SystemSetting(key="kill_switch", value="off"))
        db_session.commit()

    raw_key = "ag_test_key_customer_support"
    if not db_session.query(AgentCredential).filter(AgentCredential.key_prefix == raw_key[:10]).first():
        cs = db_session.query(Agent).filter(Agent.name == "CustomerSupportAgent").first()
        db_session.add(AgentCredential(agent_id=cs.id, key_hash=AgentCredential.hash_key(raw_key), key_prefix=raw_key[:10]))
        db_session.commit()

    return {"customer_support_key": raw_key}


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)
