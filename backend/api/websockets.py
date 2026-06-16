"""WebSocket endpoint: one connection per extension, driving one agent.

The receive loop only dispatches messages; the agent loop runs as a separate
asyncio task and talks to the browser by sending `execute_tool` requests and
awaiting futures keyed by request id.
"""

import asyncio
import json
import logging
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from core.config import settings
from schemas.schemas import ToolResult
from services.agent import AgentRunner
from services.llm import BaseLLM, create_llm
from services.memory import MemoryService
from services.vision import VisionLocator, create_vision

logger = logging.getLogger("ws")
router = APIRouter()

memory = MemoryService()
_llm: Optional[BaseLLM] = None
_vision: Optional[VisionLocator] = None
_vision_ready = False


def get_llm() -> BaseLLM:
    global _llm
    if _llm is None:
        _llm = create_llm()
    return _llm


def get_vision() -> Optional[VisionLocator]:
    global _vision, _vision_ready
    if not _vision_ready:
        _vision = create_vision()
        _vision_ready = True
        logger.info("Vision fallback: %s",
                    f"enabled ({_vision.model})" if _vision else "disabled")
    return _vision


class AgentConnection:
    """Implements BrowserBridge over one websocket."""

    def __init__(self, websocket: WebSocket, session_id: int):
        self.websocket = websocket
        self.session_id = session_id
        self.pending: Dict[str, asyncio.Future] = {}
        self.agent_task: Optional[asyncio.Task] = None

    # --- BrowserBridge ----------------------------------------------------

    async def emit(self, message: dict) -> None:
        await self.websocket.send_text(json.dumps(message))

    async def execute_tool(self, tool: str, args: dict) -> ToolResult:
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.emit({"type": "execute_tool", "id": request_id,
                             "payload": {"tool": tool, "args": args}})
            payload = await asyncio.wait_for(future, settings.TOOL_TIMEOUT_SECONDS)
            return ToolResult(ok=bool(payload.get("ok")),
                              result=payload.get("result"),
                              error=payload.get("error"))
        except asyncio.TimeoutError:
            return ToolResult(ok=False, error=f"{tool} timed out after "
                              f"{settings.TOOL_TIMEOUT_SECONDS:.0f}s")
        finally:
            self.pending.pop(request_id, None)

    async def request_confirmation(self, reason: str, tool: str, args: dict) -> bool:
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.emit({"type": "confirmation_required", "id": request_id,
                             "reason": reason, "tool": tool, "args": args})
            payload = await asyncio.wait_for(
                future, settings.CONFIRMATION_TIMEOUT_SECONDS)
            return bool(payload.get("approved"))
        except asyncio.TimeoutError:
            return False
        finally:
            self.pending.pop(request_id, None)

    # --- dispatch -----------------------------------------------------------

    def resolve(self, request_id: str, payload: dict) -> None:
        future = self.pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(payload)

    def busy(self) -> bool:
        return self.agent_task is not None and not self.agent_task.done()

    def stop_agent(self) -> bool:
        if self.busy():
            self.agent_task.cancel()
            return True
        return False


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket,
                             client_id: str = Query("anonymous"),
                             token: str = Query("")):
    # Reject connections without the shared token when one is configured.
    if settings.AGENT_TOKEN and token != settings.AGENT_TOKEN:
        await websocket.close(code=1008)  # policy violation
        logger.warning("Rejected WS connection: bad/missing token (client %s)", client_id)
        return
    await websocket.accept()
    user_id = memory.get_or_create_user(client_id)
    session_id = memory.create_session(user_id)
    conn = AgentConnection(websocket, session_id)
    await conn.emit({"type": "session_started", "session_id": session_id,
                     "user_id": user_id})
    logger.info("Client %s connected (session %s)", client_id, session_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await conn.emit({"type": "error", "message": "Invalid JSON"})
                continue

            kind = message.get("type")

            if kind == "user_request":
                content = str(message.get("content", "")).strip()
                if not content:
                    continue
                if conn.busy():
                    await conn.emit({"type": "agent_message",
                                     "content": "I'm still working on the previous task. "
                                                "Press Stop first if you want to switch."})
                    continue
                memory.add_message(session_id, "user", content)
                mode = str(message.get("mode", "dom")).lower()
                try:
                    runner = AgentRunner(get_llm(), memory, conn, session_id,
                                         vision=get_vision(), mode=mode)
                except Exception as exc:
                    await conn.emit({"type": "error",
                                     "message": f"LLM not configured: {exc}"})
                    continue
                conn.agent_task = asyncio.create_task(runner.run(content))

            elif kind in ("tool_result", "confirmation_response"):
                request_id = message.get("id", "")
                conn.resolve(request_id, message)

            elif kind == "stop":
                if conn.stop_agent():
                    logger.info("Task stopped by user (session %s)", session_id)
                else:
                    await conn.emit({"type": "agent_status", "status": "idle",
                                     "detail": ""})

            elif kind == "ping":
                await conn.emit({"type": "pong"})

    except WebSocketDisconnect:
        logger.info("Client %s disconnected (session %s)", client_id, session_id)
    finally:
        conn.stop_agent()
        for future in conn.pending.values():
            if not future.done():
                future.cancel()
