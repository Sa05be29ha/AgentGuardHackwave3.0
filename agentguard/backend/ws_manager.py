"""Broadcast live events to all connected dashboard clients over WebSocket."""
import asyncio
import json
from typing import List
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []
        self.loop = None

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(json.dumps(message, default=str))
            except Exception:
                dead.append(ws)
        for d in dead:
            self.disconnect(d)

    def broadcast_sync(self, message: dict):
        """Called from sync request-handling code."""
        if not self.active:
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.broadcast(message))
            else:
                loop.run_until_complete(self.broadcast(message))
        except RuntimeError:
            pass


manager = ConnectionManager()
