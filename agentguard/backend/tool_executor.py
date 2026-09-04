"""
Maps tool names to the actual protected-service function that performs the
action. This module is the ONLY place tool names are wired to real mutating
functions -- agents and adapters only ever know tool *names*, never the
underlying service. AgentGuard owns the connection.
"""
from mock_services import payment_service, customer_db, communication_service, git_service
import company_database


def execute_tool(tool: str, arguments: dict) -> dict:
    if tool == "get_customer":
        return customer_db.get_customer(arguments["customer_id"])
    if tool == "get_customer_orders":
        return {"orders": company_database.get_customer_orders(arguments["customer_id"])}
    if tool == "update_customer":
        args = dict(arguments)
        cid = args.pop("customer_id")
        return customer_db.update_customer(cid, **args)
    if tool == "export_customers":
        return {"records": customer_db.export_customers(arguments.get("limit", 10000))}
    if tool == "delete_customer":
        return customer_db.delete_customer(arguments["customer_id"])

    if tool == "create_support_ticket":
        cid = arguments.get("customer_id", "cust-4821")
        subj = arguments.get("subject") or arguments.get("reason") or "Customer Inquiry"
        desc = arguments.get("description", "")
        prio = arguments.get("priority", "MEDIUM")
        return company_database.create_support_ticket(cid, subj, desc, prio)

    if tool == "send_customer_email":
        return communication_service.send_customer_email(
            arguments.get("customer_id", ""), arguments.get("subject", ""), arguments.get("body", "")
        )
    if tool == "send_message":
        return communication_service.send_message(arguments.get("channel", "#general"), arguments.get("text", ""))

    if tool == "refund_customer":
        return payment_service.refund_customer(
            arguments["customer_id"], arguments["amount"],
            arguments.get("currency", "INR"), arguments.get("reason", "")
        )
    if tool == "get_invoice":
        return payment_service.get_invoice(arguments["invoice_id"])
    if tool == "create_payment":
        return payment_service.create_payment(arguments["recipient"], arguments["amount"], arguments.get("currency", "INR"))
    if tool == "get_payment":
        return payment_service.get_payment(arguments["payment_id"])
    if tool == "export_financial_records":
        return payment_service.export_all_financial_records()

    if tool == "read_repository":
        return git_service.read_repository(arguments.get("repo", "novacommerce/api"))
    if tool == "create_branch":
        return git_service.create_branch(arguments.get("repo", "novacommerce/api"), arguments.get("branch_name", "feature/new"))
    if tool == "create_pull_request":
        return git_service.create_pull_request(
            arguments.get("repo", "novacommerce/api"), arguments.get("branch_name", "feature/new"),
            arguments.get("title", "Untitled PR"),
        )
    if tool == "deploy_production":
        return git_service.deploy_production(arguments.get("version", "v0.0.0"))
    if tool == "delete_production_database":
        return git_service.delete_production_database()

    raise ValueError(f"Unknown tool '{tool}'")
