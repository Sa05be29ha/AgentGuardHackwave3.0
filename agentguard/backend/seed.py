"""
Seed realistic demo data: 3 agents with credentials, permissions, and the
policies described in the spec (support/finance/devops).

Run standalone: python seed.py
Raw API keys are printed ONCE at seed time (also written to seeded_keys.json
for the demo scripts/SDK to pick up) -- only their hash is stored in the DB.
"""
import json
import secrets
from database import SessionLocal, engine, Base
from models import Agent, AgentCredential, Policy, Tool, User, SystemSetting

Base.metadata.create_all(bind=engine)


def issue_key(db, agent: Agent) -> str:
    raw_key = "ag_" + secrets.token_hex(24)
    cred = AgentCredential(agent_id=agent.id, key_hash=AgentCredential.hash_key(raw_key), key_prefix=raw_key[:10])
    db.add(cred)
    db.commit()
    return raw_key


def seed():
    db = SessionLocal()
    if db.query(Agent).count() > 0:
        print("Already seeded. Delete agentguard.db to reseed.")
        db.close()
        return

    admin = User(name="Admin", role="admin")
    db.add(admin)

    cs_agent = Agent(
        name="CustomerSupportAgent", description="Handles customer support tasks for NovaCommerce.",
        owner="Customer Experience", status="ACTIVE", risk_level="MEDIUM",
       permissions="customer.read,order.read,ticket.create,communication.send,refund.create",
    )
    finance_agent = Agent(
        name="FinanceAgent", description="Handles finance operations for NovaCommerce.",
        owner="Finance", status="ACTIVE", risk_level="MEDIUM",
        permissions="invoice.read,payment.create",
    )
    devops_agent = Agent(
        name="DevOpsAgent", description="Handles software development and infrastructure for NovaCommerce.",
        owner="Engineering", status="ACTIVE", risk_level="HIGH",
        permissions="repository.read,repository.write,deployment.create",
    )
    db.add_all([cs_agent, finance_agent, devops_agent])
    db.commit()
    for a in (cs_agent, finance_agent, devops_agent):
        db.refresh(a)

    keys = {}
    keys[cs_agent.name] = issue_key(db, cs_agent)
    keys[finance_agent.name] = issue_key(db, finance_agent)
    keys[devops_agent.name] = issue_key(db, devops_agent)

    policies = [
        # CustomerSupportAgent
        Policy(name="Support Refund Autolimit", description="Support agents may refund up to ₹5,000 autonomously.",
               agent_id=cs_agent.id, tool="refund_customer", effect="ALLOW",
               condition_field="amount", condition_operator="lte", condition_value=5000, priority=10),
              Policy(name="Support Order Lookup", description="Support agents may view a customer's order history.",
                     agent_id=cs_agent.id, tool="get_customer_orders", effect="ALLOW", priority=10),
        Policy(name="Support Refund Approval", description="Refunds above ₹5,000 require manager approval.",
               agent_id=cs_agent.id, tool="refund_customer", effect="REQUIRE_APPROVAL",
               condition_field="amount", condition_operator="gt", condition_value=5000, priority=20),
        Policy(name="Customer Data Export Protection",
               description="Support agents cannot export customer data (RESTRICTED classification).",
               agent_id=cs_agent.id, tool="export_customers", effect="DENY",
               condition_field="always", condition_operator="always", priority=1),
        Policy(name="No Customer Deletion", description="Support agents cannot delete customer records.",
               agent_id=cs_agent.id, tool="delete_customer", effect="DENY", priority=1),
        Policy(name="No Security Setting Changes", description="Support agents cannot modify security settings.",
               agent_id=cs_agent.id, tool="modify_security_settings", effect="DENY", priority=1),

        # FinanceAgent
        Policy(name="Finance Payment Autolimit", description="Finance agents may create payments up to ₹50,000 autonomously.",
               agent_id=finance_agent.id, tool="create_payment", effect="ALLOW",
               condition_field="amount", condition_operator="lte", condition_value=50000, priority=10),
        Policy(name="Finance Payment Approval", description="Payments above ₹50,000 require approval.",
               agent_id=finance_agent.id, tool="create_payment", effect="REQUIRE_APPROVAL",
               condition_field="amount", condition_operator="gt", condition_value=50000, priority=20),
        Policy(name="No Financial Record Export", description="Finance agents cannot export all financial records.",
               agent_id=finance_agent.id, tool="export_financial_records", effect="DENY", priority=1),
        Policy(name="No Accounting Config Changes", description="Finance agents cannot modify accounting configuration.",
               agent_id=finance_agent.id, tool="modify_accounting_configuration", effect="DENY", priority=1),

        # DevOpsAgent
        Policy(name="Production Deploy Approval", description="Production deployments require human approval.",
               agent_id=devops_agent.id, tool="deploy_production", effect="REQUIRE_APPROVAL",
               condition_field="always", condition_operator="always", priority=10),
        Policy(name="No Production DB Deletion", description="Production database deletion is never permitted autonomously.",
               agent_id=devops_agent.id, tool="delete_production_database", effect="DENY", priority=1),
        Policy(name="No Disabling Security Controls", description="Security controls cannot be disabled.",
               agent_id=devops_agent.id, tool="disable_security_controls", effect="DENY", priority=1),
    ]
    db.add_all(policies)

    tools = [
        Tool(name="get_customer", resource_action="customer.read", data_classification="INTERNAL"),
       Tool(name="get_customer_orders", resource_action="order.read", data_classification="INTERNAL"),
        Tool(name="update_customer", resource_action="customer.update", data_classification="INTERNAL"),
        Tool(name="export_customers", resource_action="customer.export", data_classification="RESTRICTED"),
        Tool(name="delete_customer", resource_action="customer.delete", data_classification="RESTRICTED", destructive=True),
        Tool(name="create_support_ticket", resource_action="ticket.create", data_classification="INTERNAL"),
        Tool(name="send_customer_email", resource_action="communication.send", data_classification="CONFIDENTIAL"),
        Tool(name="send_message", resource_action="communication.send", data_classification="INTERNAL"),
        Tool(name="refund_customer", resource_action="refund.create", data_classification="RESTRICTED"),
        Tool(name="get_invoice", resource_action="invoice.read", data_classification="CONFIDENTIAL"),
        Tool(name="create_payment", resource_action="payment.create", data_classification="RESTRICTED"),
        Tool(name="get_payment", resource_action="payment.read", data_classification="CONFIDENTIAL"),
        Tool(name="export_financial_records", resource_action="financial.export", data_classification="RESTRICTED"),
        Tool(name="read_repository", resource_action="repository.read", data_classification="INTERNAL"),
        Tool(name="create_branch", resource_action="repository.write", data_classification="INTERNAL"),
        Tool(name="create_pull_request", resource_action="repository.write", data_classification="INTERNAL"),
        Tool(name="deploy_production", resource_action="deployment.create", data_classification="RESTRICTED"),
        Tool(name="delete_production_database", resource_action="database.delete", data_classification="RESTRICTED", destructive=True),
        Tool(name="disable_security_controls", resource_action="security.modify", data_classification="RESTRICTED", destructive=True),
        Tool(name="modify_security_settings", resource_action="security.modify", data_classification="RESTRICTED"),
        Tool(name="modify_accounting_configuration", resource_action="accounting.modify", data_classification="RESTRICTED"),
    ]
    db.add_all(tools)

    db.add(SystemSetting(key="kill_switch", value="off"))
    db.commit()
    db.close()

    with open("seeded_keys.json", "w") as f:
        json.dump(keys, f, indent=2)

    print("Seed complete. API keys (also saved to seeded_keys.json):")
    for name, key in keys.items():
        print(f"  {name}: {key}")


if __name__ == "__main__":
    seed()
