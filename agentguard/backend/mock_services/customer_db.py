"""
Customer Database Service Adapter.

Connects AgentGuard tool execution to the real NovaCommerce Company Database.
Protected by AgentGuard Gateway: actions only reach here if permitted by policy.
"""
from typing import Dict, List, Any
import company_database


def get_customer(customer_id: str) -> Dict[str, Any]:
    return company_database.get_customer(customer_id)


def update_customer(customer_id: str, **fields) -> Dict[str, Any]:
    return company_database.update_customer(customer_id, **fields)


def export_customers(limit: int = 10000) -> List[Dict[str, Any]]:
    """RESTRICTED. If this executes, unauthorized data exfiltration occurred."""
    return company_database.export_customers(limit=limit)


def delete_customer(customer_id: str) -> Dict[str, Any]:
    """RESTRICTED / DESTRUCTIVE."""
    return company_database.delete_customer(customer_id)
