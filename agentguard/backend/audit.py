"""Tamper-evident audit logging (hash chain) + human-readable explanations."""
import json
import datetime as dt
from sqlalchemy.orm import Session
from models import AuditEvent


def _last_hash(db: Session) -> str:
    last = db.query(AuditEvent).order_by(AuditEvent.seq.desc()).first()
    return last.current_event_hash if last else "GENESIS"


def record_event(db: Session, **kwargs) -> AuditEvent:
    prev_hash = _last_hash(db)
    # Set created_at explicitly (rather than relying on the column default,
    # which only fires at flush time) so compute_hash() below hashes the
    # exact same value that will later be persisted and re-read.
    from models import gen_id
    evt = AuditEvent(
        id=gen_id("evt"), previous_event_hash=prev_hash, created_at=dt.datetime.utcnow(), **kwargs
    )
    evt.current_event_hash = evt.compute_hash()
    db.add(evt)
    db.commit()
    db.refresh(evt)
    return evt


def verify_chain(db: Session):
    """Walk the whole audit log and verify no event has been tampered with."""
    events = db.query(AuditEvent).order_by(AuditEvent.seq.asc()).all()
    prev = "GENESIS"
    for evt in events:
        if evt.previous_event_hash != prev:
            return False, f"Chain broken at event {evt.id}: previous_event_hash mismatch"
        if evt.compute_hash() != evt.current_event_hash:
            return False, f"Chain broken at event {evt.id}: content hash mismatch (tampered)"
        prev = evt.current_event_hash
    return True, f"Verified {len(events)} events -- chain intact"


def explain_block(reason: str, tool: str, arguments: dict) -> str:
    return (
        f"BLOCKED: {reason} "
        f"(requested tool='{tool}', arguments={json.dumps(arguments)})"
    )
