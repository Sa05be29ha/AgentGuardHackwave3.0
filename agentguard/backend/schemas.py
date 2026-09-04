"""Pydantic request/response schemas."""
from typing import Optional, Dict, Any, List
from pydantic import BaseModel


class GatewayActionRequest(BaseModel):
    agent_id: str
    user_id: Optional[str] = ""
    tool: str
    arguments: Dict[str, Any] = {}
    context: Dict[str, Any] = {}


class GatewayActionResponse(BaseModel):
    request_id: str
    action_request_id: str
    decision: str
    status: str
    risk_score: int
    risk_level: str
    reason: str
    approval_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class AgentCreate(BaseModel):
    name: str
    description: str = ""
    owner: str = ""
    permissions: List[str] = []


class AgentUpdate(BaseModel):
    status: Optional[str] = None
    permissions: Optional[List[str]] = None
    description: Optional[str] = None


class PolicyCreate(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    priority: int = 100
    agent_id: Optional[str] = None
    tool: str
    effect: str  # ALLOW / DENY / REQUIRE_APPROVAL
    condition_field: str = "always"
    condition_operator: str = "always"
    condition_value: Optional[float] = None


class PolicyUpdate(BaseModel):
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    effect: Optional[str] = None
    condition_operator: Optional[str] = None
    condition_value: Optional[float] = None


class PolicySimulateRequest(BaseModel):
    agent_id: str
    tool: str
    arguments: Dict[str, Any] = {}


class ApprovalDecision(BaseModel):
    approved_by: str = "admin"
    reason: Optional[str] = None


class PolicyAssistRequest(BaseModel):
    instruction: str
    agent_name: Optional[str] = None
    model: Optional[str] = None


class AgentRunRequest(BaseModel):
    instruction: str
    agent_name: Optional[str] = "CustomerSupportAgent"
    model: Optional[str] = None
    provider: Optional[str] = None


class LLMConfigRequest(BaseModel):
    api_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    provider: Optional[str] = None


class LLMTestRequest(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class SecurityAnalysisRequest(BaseModel):
    tool: str
    arguments: Dict[str, Any] = {}
    instruction: Optional[str] = ""
    agent_name: Optional[str] = ""
    model: Optional[str] = None


class CustomerChatMessageRequest(BaseModel):
    message: str
    customer_id: Optional[str] = "cust-4821"
    conversation_id: Optional[str] = "conv-customer-web"
    model: Optional[str] = None
    provider: Optional[str] = None


class CustomerChatMessageResponse(BaseModel):
    reply: str
    workflow: Dict[str, Any]

