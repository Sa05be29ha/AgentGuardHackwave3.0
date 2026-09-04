"""
Payment Service Adapter.

Connects AgentGuard tool execution to the real NovaCommerce Company Database.
Protected by AgentGuard Gateway: actions only reach here if permitted by policy.
"""
import uuid
import datetime as dt
from typing import Dict, List, Any
import company_database

_PAYMENTS: Dict[str, Any] = {}


def get_invoice(invoice_id: str) -> Dict[str, Any]:
    return company_database.get_invoice(invoice_id)


def refund_customer(customer_id: str, amount: float, currency: str = "INR", reason: str = "") -> Dict[str, Any]:
    """Actually mutates transaction state in the company database."""
    return company_database.refund_customer(customer_id=customer_id, amount=amount, currency=currency, reason=reason)


def create_payment(recipient: str, amount: float, currency: str = "INR") -> Dict[str, Any]:
    payment_id = f"pay_{uuid.uuid4().hex[:8]}"
    payment = {
        "payment_id": payment_id,
        "recipient": recipient,
        "amount": amount,
        "currency": currency,
        "status": "processed",
        "processed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    _PAYMENTS[payment_id] = payment
    return payment


def get_payment(payment_id: str) -> Dict[str, Any]:
    p = _PAYMENTS.get(payment_id)
    if not p:
        raise ValueError(f"payment {payment_id} not found")
    return p


def export_all_financial_records() -> Dict[str, Any]:
    """Highly sensitive -- should virtually never be reached by policy."""
    records = company_database.export_financial_records()
    records["payments"] = list(_PAYMENTS.values())
    return records


def all_transactions() -> List[Dict[str, Any]]:
    return company_database.get_all_transactions()
