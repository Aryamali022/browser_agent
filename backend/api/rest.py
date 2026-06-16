"""REST endpoints for history: sessions, messages, tasks and tool logs.

The side panel uses these to show previous conversations and task history;
they are also handy for debugging with curl.
"""

from typing import Optional

from fastapi import APIRouter

from services.memory import MemoryService

router = APIRouter(prefix="/api")
memory = MemoryService()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/users/{client_id}/sessions")
def list_sessions(client_id: str):
    user_id = memory.get_or_create_user(client_id)
    return {"user_id": user_id, "sessions": memory.list_sessions(user_id)}


@router.get("/users/{client_id}/tasks")
def list_user_tasks(client_id: str):
    user_id = memory.get_or_create_user(client_id)
    return {"user_id": user_id, "tasks": memory.list_tasks(user_id=user_id)}


@router.delete("/users/{client_id}/history")
def clear_history(client_id: str, keep_session: Optional[int] = None):
    """Wipe the user's saved tasks, logs and past conversations.

    Pass ?keep_session=<id> to preserve the live session's messages so the
    running conversation keeps its context.
    """
    user_id = memory.get_or_create_user(client_id)
    deleted = memory.clear_history(user_id, keep_session_id=keep_session)
    return {"user_id": user_id, "deleted_rows": deleted}


@router.get("/sessions/{session_id}/messages")
def list_messages(session_id: int):
    return {"session_id": session_id, "messages": memory.list_messages(session_id)}


@router.get("/sessions/{session_id}/tasks")
def list_session_tasks(session_id: int):
    return {"session_id": session_id, "tasks": memory.list_tasks(session_id=session_id)}


@router.get("/tasks/{task_id}/logs")
def list_tool_logs(task_id: int):
    return {"task_id": task_id, "logs": memory.list_tool_logs(task_id)}
