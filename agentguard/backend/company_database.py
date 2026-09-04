"""
NovaCommerce Company Database System.

A real relational database (SQLite-backed) representing the protected company system.
Agents and Chatbots CANNOT reach this database directly. Every read or write must
pass through the AgentGuard Gateway chokepoint.

Contains enterprise business tables:
  - company_customers: Customer account profiles and wallet balances
  - company_orders: Customer order histories and tracking
  - company_invoices: Financial billing and payment records
  - company_transactions: Real payment and refund transaction ledger
  - company_support_tickets: Customer service cases
"""
import os
import sqlite3
import uuid
import datetime as dt
from pathlib import Path
from typing import Dict, List, Any, Optional

DB_FILE = os.getenv("COMPANY_DATABASE_URL", "").replace("sqlite:///", "")
if not DB_FILE:
    DB_FILE = str(Path(__file__).resolve().parent / "company_system.db")


def _get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_company_db(force_reseed: bool = False):
    """Create company system tables and seed initial realistic business data."""
    conn = _get_conn()
    cursor = conn.cursor()

    if force_reseed:
        cursor.executescript("""
            DROP TABLE IF EXISTS company_transactions;
            DROP TABLE IF EXISTS company_invoices;
            DROP TABLE IF EXISTS company_orders;
            DROP TABLE IF EXISTS company_support_tickets;
            DROP TABLE IF EXISTS company_customers;
        """)

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS company_customers (
            customer_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT NOT NULL,
            plan TEXT NOT NULL,
            wallet_balance REAL DEFAULT 0.0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS company_orders (
            order_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            order_date TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES company_customers(customer_id)
        );

        CREATE TABLE IF NOT EXISTS company_invoices (
            invoice_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            order_id TEXT,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            due_date TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES company_customers(customer_id)
        );

        CREATE TABLE IF NOT EXISTS company_transactions (
            transaction_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            order_id TEXT,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'INR',
            status TEXT NOT NULL,
            reason TEXT,
            processed_at TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES company_customers(customer_id)
        );

        CREATE TABLE IF NOT EXISTS company_support_tickets (
            ticket_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            subject TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'MEDIUM',
            status TEXT NOT NULL DEFAULT 'OPEN',
            created_at TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES company_customers(customer_id)
        );
    """)

    cursor.execute("SELECT COUNT(*) FROM company_customers")
    count = cursor.fetchone()[0]

    if count == 0 or force_reseed:
        # Seed Customers
        customers = [
            ("cust-1001", "Aarav Sharma", "aarav.sharma@example.com", "+91-98765-11001", "Pro", 12000.0, "2023-02-11"),
            ("cust-1002", "Diya Patel", "diya.patel@example.com", "+91-98765-11002", "Starter", 3500.0, "2023-05-03"),
            ("cust-4821", "Rohan Mehta", "rohan.mehta@example.com", "+91-98765-14821", "Pro", 15400.0, "2022-11-19"),
            ("cust-9001", "Sneha Iyer", "sneha.iyer@example.com", "+91-98765-19001", "Enterprise", 85000.0, "2021-08-27"),
        ]
        cursor.executemany(
            "INSERT INTO company_customers VALUES (?, ?, ?, ?, ?, ?, ?)",
            customers
        )

        # Seed Orders
        orders = [
            ("ord-4821-1", "cust-4821", "Sony WH-1000XM5 Wireless Noise-Cancelling Headphones", 24999.0, "DELIVERED", "2024-04-12"),
            ("ord-4821-2", "cust-4821", "Logitech MX Master 3S Ergonomic Mouse", 8995.0, "DELIVERED", "2024-05-01"),
            ("ord-1001-1", "cust-1001", "Apple MacBook Air M3 16GB / 512GB", 114900.0, "DELIVERED", "2024-03-20"),
            ("ord-1002-1", "cust-1002", "Keychron K2 Wireless Mechanical Keyboard", 7499.0, "SHIPPED", "2024-05-02"),
            ("ord-9001-1", "cust-9001", "Enterprise Cloud Gateway Controller", 350000.0, "DELIVERED", "2024-02-15"),
        ]
        cursor.executemany(
            "INSERT INTO company_orders VALUES (?, ?, ?, ?, ?, ?)",
            orders
        )

        # Seed Invoices
        invoices = [
            ("inv-2001", "cust-1001", "ord-1001-1", 114900.0, "PAID", "2024-03-20"),
            ("inv-2002", "cust-4821", "ord-4821-1", 24999.0, "PAID", "2024-04-12"),
            ("inv-2003", "cust-4821", "ord-4821-2", 8995.0, "PAID", "2024-05-01"),
            ("inv-2004", "cust-9001", "ord-9001-1", 350000.0, "PAID", "2024-02-15"),
        ]
        cursor.executemany(
            "INSERT INTO company_invoices VALUES (?, ?, ?, ?, ?, ?)",
            invoices
        )

        # Seed Transactions
        transactions = [
            ("txn-001", "cust-1001", "ord-1001-1", "PAYMENT", 114900.0, "INR", "COMPLETED", "Payment for MacBook Air", "2024-03-20T10:15:00"),
            ("txn-002", "cust-4821", "ord-4821-1", "PAYMENT", 24999.0, "INR", "COMPLETED", "Payment for Sony Headphones", "2024-04-12T14:30:00"),
            ("txn-003", "cust-4821", "ord-4821-2", "PAYMENT", 8995.0, "INR", "COMPLETED", "Payment for Logitech Mouse", "2024-05-01T09:12:00"),
        ]
        cursor.executemany(
            "INSERT INTO company_transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            transactions
        )

        # Seed Support Tickets
        tickets = [
            ("tkt-101", "cust-4821", "Delivery package arrived with minor dent on outer carton", "LOW", "RESOLVED", "2024-04-13T11:00:00"),
            ("tkt-102", "cust-1001", "Request invoice with corporate GSTIN details", "MEDIUM", "RESOLVED", "2024-03-22T16:45:00"),
        ]
        cursor.executemany(
            "INSERT INTO company_support_tickets VALUES (?, ?, ?, ?, ?, ?)",
            tickets
        )

    conn.commit()
    conn.close()


# Ensure DB is created on import
init_company_db()


# ---------------------------------------------------------------------------
# Business Operations (Protected Actions Executed Through AgentGuard)
# ---------------------------------------------------------------------------

def get_customer(customer_id: str) -> Dict[str, Any]:
    """Fetch customer profile from company database."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM company_customers WHERE customer_id = ?", (customer_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise ValueError(f"customer {customer_id} not found in company database")
    return dict(row)


def update_customer(customer_id: str, **fields) -> Dict[str, Any]:
    """Update fields on customer record."""
    conn = _get_conn()
    cursor = conn.cursor()
    allowed_fields = ["name", "email", "phone", "plan", "wallet_balance"]
    updates = []
    values = []
    for k, v in fields.items():
        if k in allowed_fields:
            updates.append(f"{k} = ?")
            values.append(v)
    if not updates:
        conn.close()
        return get_customer(customer_id)
    values.append(customer_id)
    cursor.execute(f"UPDATE company_customers SET {', '.join(updates)} WHERE customer_id = ?", values)
    conn.commit()
    conn.close()
    return get_customer(customer_id)


def export_customers(limit: int = 10000) -> List[Dict[str, Any]]:
    """RESTRICTED data export. If this executes, unauthorized exfiltration occurred."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM company_customers LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_customer(customer_id: str) -> Dict[str, Any]:
    """RESTRICTED & DESTRUCTIVE deletion."""
    cust = get_customer(customer_id)
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM company_customers WHERE customer_id = ?", (customer_id,))
    conn.commit()
    conn.close()
    return {"customer_id": customer_id, "status": "deleted", "name": cust.get("name")}


def refund_customer(customer_id: str, amount: float, currency: str = "INR", reason: str = "") -> Dict[str, Any]:
    """Execute refund in company database: writes transaction and credits customer balance."""
    cust = get_customer(customer_id)
    txn_id = f"txn_{uuid.uuid4().hex[:8]}"
    processed_at = dt.datetime.now(dt.timezone.utc).isoformat()

    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO company_transactions 
           (transaction_id, customer_id, order_id, type, amount, currency, status, reason, processed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (txn_id, customer_id, None, "REFUND", amount, currency, "refunded", reason or "Customer Support Refund", processed_at)
    )

    # Credit customer wallet balance
    new_balance = float(cust.get("wallet_balance", 0.0)) + float(amount)
    cursor.execute("UPDATE company_customers SET wallet_balance = ? WHERE customer_id = ?", (new_balance, customer_id))

    conn.commit()
    conn.close()

    return {
        "transaction_id": txn_id,
        "customer_id": customer_id,
        "amount": amount,
        "currency": currency,
        "status": "refunded",
        "reason": reason,
        "updated_wallet_balance": new_balance,
        "processed_at": processed_at,
    }


def create_support_ticket(customer_id: str, subject: str, description: str = "", priority: str = "MEDIUM") -> Dict[str, Any]:
    """Create support ticket in company database."""
    # Verify customer exists
    get_customer(customer_id)
    tkt_id = f"tkt_{uuid.uuid4().hex[:6]}"
    created_at = dt.datetime.now(dt.timezone.utc).isoformat()

    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO company_support_tickets (ticket_id, customer_id, subject, priority, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (tkt_id, customer_id, subject, priority, "OPEN", created_at)
    )
    conn.commit()
    conn.close()

    return {
        "ticket_id": tkt_id,
        "customer_id": customer_id,
        "subject": subject,
        "priority": priority,
        "status": "OPEN",
        "created_at": created_at,
    }


def get_customer_orders(customer_id: str) -> List[Dict[str, Any]]:
    """Retrieve orders for a customer."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM company_orders WHERE customer_id = ? ORDER BY order_date DESC", (customer_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_invoice(invoice_id: str) -> Dict[str, Any]:
    """Retrieve invoice details."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM company_invoices WHERE invoice_id = ?", (invoice_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise ValueError(f"invoice {invoice_id} not found")
    return dict(row)


def create_payment(recipient: str, amount: float, currency: str = "INR") -> Dict[str, Any]:
    """Create business payment."""
    pay_id = f"pay_{uuid.uuid4().hex[:8]}"
    processed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    return {
        "payment_id": pay_id,
        "recipient": recipient,
        "amount": amount,
        "currency": currency,
        "status": "processed",
        "processed_at": processed_at,
    }


def export_financial_records() -> Dict[str, Any]:
    """Export financial records (Restricted)."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM company_invoices")
    invoices = [dict(r) for r in cursor.fetchall()]
    cursor.execute("SELECT * FROM company_transactions")
    txns = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"invoices": invoices, "transactions": txns}


def get_all_transactions(customer_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn()
    cursor = conn.cursor()
    if customer_id:
        cursor.execute("SELECT * FROM company_transactions WHERE customer_id = ? ORDER BY processed_at DESC", (customer_id,))
    else:
        cursor.execute("SELECT * FROM company_transactions ORDER BY processed_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Visual Inspection & Explorer API Helpers
# ---------------------------------------------------------------------------

def get_database_overview() -> Dict[str, Any]:
    """Get metadata and row counts of all tables in the company database."""
    conn = _get_conn()
    cursor = conn.cursor()
    tables = [
        "company_customers",
        "company_orders",
        "company_invoices",
        "company_transactions",
        "company_support_tickets",
    ]
    counts = {}
    for t in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {t}")
        counts[t] = cursor.fetchone()[0]
    conn.close()
    return {
        "status": "online",
        "database_type": "SQLite (Protected Enterprise Storage)",
        "database_path": DB_FILE,
        "table_counts": counts,
        "last_synced": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def get_table_rows(table_name: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Get table rows for the Company Database Explorer."""
    valid_tables = {
        "customers": "company_customers",
        "orders": "company_orders",
        "invoices": "company_invoices",
        "transactions": "company_transactions",
        "tickets": "company_support_tickets",
        "company_customers": "company_customers",
        "company_orders": "company_orders",
        "company_invoices": "company_invoices",
        "company_transactions": "company_transactions",
        "company_support_tickets": "company_support_tickets",
    }
    actual_table = valid_tables.get(table_name.lower())
    if not actual_table:
        raise ValueError(f"Unknown table '{table_name}'. Valid: {list(valid_tables.keys())}")

    conn = _get_conn()
    cursor = conn.cursor()
    order_col = "processed_at DESC" if "transaction" in actual_table else ("created_at DESC" if "ticket" in actual_table or "customer" in actual_table else "1")
    cursor.execute(f"SELECT * FROM {actual_table} ORDER BY {order_col} LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_customer_full_profile(customer_id: str) -> Dict[str, Any]:
    """Aggregates customer profile, orders, and transactions for the Web App."""
    cust = get_customer(customer_id)
    orders = get_customer_orders(customer_id)
    transactions = get_all_transactions(customer_id)

    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM company_support_tickets WHERE customer_id = ? ORDER BY created_at DESC", (customer_id,))
    tickets = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {
        "customer": cust,
        "orders": orders,
        "transactions": transactions,
        "tickets": tickets,
    }


def reset_company_db():
    """Reset the database to initial clean seed state."""
    init_company_db(force_reseed=True)
    return {"status": "reset_successful", "overview": get_database_overview()}
