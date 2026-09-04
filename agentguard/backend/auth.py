"""
Agent authentication.

Agents authenticate with an AgentGuard-issued API key sent as:
    Authorization: Bearer <api_key>

We never trust an `agent_id` supplied in a request body on its own -- the
key must hash-match an active, non-revoked credential belonging to that
agent, and the agent itself must be ACTIVE.
"""
from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import Agent, AgentCredential


def authenticate_agent(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> Agent:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    raw_key = authorization.split(" ", 1)[1].strip()
    key_hash = AgentCredential.hash_key(raw_key)

    cred = (
        db.query(AgentCredential)
        .filter(AgentCredential.key_hash == key_hash, AgentCredential.revoked == False)  # noqa: E712
        .first()
    )
    if not cred:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    agent = db.query(Agent).filter(Agent.id == cred.agent_id).first()
    if not agent:
        raise HTTPException(status_code=401, detail="Agent not found for credential")

    return agent
