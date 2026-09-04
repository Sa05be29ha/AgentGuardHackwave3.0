"""
Tiny AgentGuard Python SDK.

This SDK does ZERO authorization logic -- it is a thin HTTP client. The
backend gateway is the sole authority on ALLOW/DENY/APPROVAL decisions.
An agent (or a raw curl/requests call) MUST go through /gateway/action;
nothing here special-cases that.
"""
import time
import requests


class AgentGuard:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def execute(self, tool: str, arguments: dict, user_id: str = "", context: dict = None, agent_id: str = ""):
        payload = {
            "agent_id": agent_id, "user_id": user_id, "tool": tool,
            "arguments": arguments, "context": context or {},
        }
        resp = requests.post(f"{self.base_url}/gateway/action", json=payload,
                              headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def wait_for_approval(self, action_request_id: str, poll_interval: float = 1.0, max_wait: float = 60.0):
        """Poll until a pending action is resolved (approved & executed, or denied)."""
        waited = 0.0
        while waited < max_wait:
            resp = requests.get(f"{self.base_url}/actions/{action_request_id}",
                                 headers=self._headers(), timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if data["status"] not in ("RECEIVED", "WAITING_APPROVAL"):
                return data
            time.sleep(poll_interval)
            waited += poll_interval
        return {"status": "TIMEOUT"}

    def health_check(self):
        resp = requests.get(f"{self.base_url}/system/status", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_agent(self, agent_id: str):
        resp = requests.get(f"{self.base_url}/agents/{agent_id}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
