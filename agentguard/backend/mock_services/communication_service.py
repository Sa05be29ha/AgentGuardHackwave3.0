"""Mock Slack / customer-communication service. Sent messages are actually stored."""
import uuid
import datetime as dt

_MESSAGES = []


def send_message(channel: str, text: str):
    msg = {"id": f"msg_{uuid.uuid4().hex[:8]}", "channel": channel, "text": text,
           "sent_at": dt.datetime.utcnow().isoformat()}
    _MESSAGES.append(msg)
    return msg


def send_customer_email(customer_id: str, subject: str, body: str):
    msg = {"id": f"email_{uuid.uuid4().hex[:8]}", "customer_id": customer_id, "subject": subject,
           "body": body, "sent_at": dt.datetime.utcnow().isoformat()}
    _MESSAGES.append(msg)
    return msg


def all_messages():
    return list(_MESSAGES)
