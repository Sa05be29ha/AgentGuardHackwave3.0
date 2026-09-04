"""
SQLAlchemy models for AgentGuard.

Core entities:
  User, Agent, AgentCredential, Policy, ActionRequest, PolicyEvaluation,
  ApprovalRequest, AuditEvent, RiskAssessment, Tool, SystemSetting.
"""
import uuid
import json
import hashlib
import datetime as dt

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text, ForeignKey
)
from sqlalchemy.orm import relationship

from database import Base


def gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> dt.datetime:
    return dt.datetime.utcnow()


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: gen_id("user"))
    name = Column(String, nullable=False)
    role = Column(String, default="admin")  # admin / approver / viewer
    created_at = Column(DateTime, default=now)


class Agent(Base):
    __tablename__ = "agents"
    id = Column(String, primary_key=True, default=lambda: gen_id("agent"))
    name = Column(String, unique=True, nullable=False)
    description = Column(Text, default="")
    owner = Column(String, default="")
    status = Column(String, default="ACTIVE")  # ACTIVE / DISABLED
    risk_level = Column(String, default="LOW")
    # comma-separated resource.action permission strings, e.g. "customer.read,ticket.create"
    permissions = Column(Text, default="")
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=now)

    credentials = relationship("AgentCredential", back_populates="agent")

    def permission_list(self):
        return [p.strip() for p in self.permissions.split(",") if p.strip()]

    def has_permission(self, perm: str) -> bool:
        return perm in self.permission_list()


class AgentCredential(Base):
    """API key credential for an agent. We store a SHA-256 hash, never the raw key."""
    __tablename__ = "agent_credentials"
    id = Column(String, primary_key=True, default=lambda: gen_id("cred"))
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    key_hash = Column(String, nullable=False, index=True)
    key_prefix = Column(String, nullable=False)  # first 8 chars shown to user for identification
    created_at = Column(DateTime, default=now)
    revoked = Column(Boolean, default=False)

    agent = relationship("Agent", back_populates="credentials")

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()


class Policy(Base):
    """
    A single authorization rule.

    agent_id = NULL   -> applies to all agents
    tool     = tool/action name this policy governs (e.g. "refund_customer")
    effect   = ALLOW | DENY | REQUIRE_APPROVAL
    condition_field / operator / value describe an optional numeric condition,
    e.g. field="amount", operator="gt", value=5000  ==  amount > 5000
    operator "always" means the condition always matches.
    """
    __tablename__ = "policies"
    id = Column(String, primary_key=True, default=lambda: gen_id("policy"))
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=100)  # lower number = evaluated first for display order
    agent_id = Column(String, ForeignKey("agents.id"), nullable=True)
    tool = Column(String, nullable=False)
    effect = Column(String, nullable=False)  # ALLOW / DENY / REQUIRE_APPROVAL
    condition_field = Column(String, default="always")
    condition_operator = Column(String, default="always")  # gt, gte, lt, lte, eq, always
    condition_value = Column(Float, nullable=True)
    created_at = Column(DateTime, default=now)


class ActionRequest(Base):
    __tablename__ = "action_requests"
    id = Column(String, primary_key=True, default=lambda: gen_id("req"))
    request_id = Column(String, index=True)  # trace id shared across pipeline
    agent_id = Column(String, ForeignKey("agents.id"))
    user_id = Column(String, default="")
    tool = Column(String, nullable=False)
    arguments_json = Column(Text, default="{}")
    context_json = Column(Text, default="{}")
    decision = Column(String, default="PENDING")  # ALLOW / DENY / REQUIRE_APPROVAL / PENDING
    status = Column(String, default="RECEIVED")  # RECEIVED / EXECUTED / BLOCKED / WAITING_APPROVAL / DENIED / FAILED
  
    result_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=now)

    def arguments(self):
        return json.loads(self.arguments_json or "{}")


class PolicyEvaluation(Base):
    __tablename__ = "policy_evaluations"
    id = Column(String, primary_key=True, default=lambda: gen_id("eval"))
    action_request_id = Column(String, ForeignKey("action_requests.id"))
    policy_id = Column(String, nullable=True)
    policy_name = Column(String, default="")
    matched = Column(Boolean, default=False)
    effect = Column(String, default="")
    explanation = Column(Text, default="")
    created_at = Column(DateTime, default=now)


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    id = Column(String, primary_key=True, default=lambda: gen_id("risk"))
    action_request_id = Column(String, ForeignKey("action_requests.id"))
    score = Column(Integer, default=0)
    level = Column(String, default="LOW")
    factors_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=now)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id = Column(String, primary_key=True, default=lambda: gen_id("appr"))
    action_request_id = Column(String, ForeignKey("action_requests.id"))
    requested_by_agent = Column(String, default="")
    requested_by_user = Column(String, default="")
    action = Column(String, default="")
    arguments_json = Column(Text, default="{}")
    reason = Column(Text, default="")
    risk_score = Column(Integer, default=0)
    risk_level = Column(String, default="LOW")
    status = Column(String, default="PENDING")  # PENDING / APPROVED / DENIED / EXPIRED
    requested_at = Column(DateTime, default=now)
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)


class AuditEvent(Base):
    """
    Tamper-evident audit log. Each event's hash is calculated from its own
    content plus the previous event's hash, forming a hash chain -- any edit
    to historical data breaks the chain from that point forward.
    """
    __tablename__ = "audit_events"
    # `seq` is the true ordering key (monotonic autoincrement) -- created_at
    # can tie at sqlite's microsecond resolution under fast test execution,
    # so the hash chain must walk events in `seq` order, not `created_at` order.
    seq = Column(Integer, primary_key=True, autoincrement=True)
    id = Column(String, unique=True, index=True, default=lambda: gen_id("evt"))
    request_id = Column(String, index=True)
    event_type = Column(String, nullable=False)
    agent_id = Column(String, default="")
    agent_name = Column(String, default="")
    user_id = Column(String, default="")
    tool = Column(String, default="")
    resource = Column(String, default="")
    arguments_json = Column(Text, default="{}")
    decision = Column(String, default="")
    risk_score = Column(Integer, default=0)
    risk_level = Column(String, default="")
    policies_triggered = Column(Text, default="")
    execution_status = Column(String, default="")
    approver = Column(String, default="")
    result_json = Column(Text, default="{}")
    message = Column(Text, default="")
    previous_event_hash = Column(String, default="")
    current_event_hash = Column(String, default="")
    created_at = Column(DateTime, default=now)

    def compute_hash(self) -> str:
        payload = json.dumps({
            "id": self.id, "request_id": self.request_id or "", "event_type": self.event_type or "",
            "agent_id": self.agent_id or "", "tool": self.tool or "", "decision": self.decision or "",
            "risk_score": self.risk_score or 0, "created_at": str(self.created_at),
            "previous_event_hash": self.previous_event_hash or "",
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


class Tool(Base):
    """Registry of protected tools and their sensitivity classification."""
    __tablename__ = "tools"
    id = Column(String, primary_key=True, default=lambda: gen_id("tool"))
    name = Column(String, unique=True, nullable=False)
    resource_action = Column(String, nullable=False)  # e.g. "refund.create"
    data_classification = Column(String, default="INTERNAL")  # PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED
    destructive = Column(Boolean, default=False)
    description = Column(Text, default="")


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key = Column(String, primary_key=True)
    value = Column(String, default="")
