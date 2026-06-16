"""Persistence layer: users, sessions, conversation messages, tasks and tool logs.

Each method opens its own short-lived DB session so the service is safe to
call from async code via threads. SQLite keeps this simple; the interface is
what the rest of the system depends on, so the store can be swapped later.
"""

import json
from typing import List, Optional, Tuple

from core.database import SessionLocal
from models.base import User, Session, Task, Message, ToolLog


class MemoryService:
    def __init__(self, session_factory=SessionLocal):
        self._factory = session_factory

    # --- users / sessions -------------------------------------------------

    def get_or_create_user(self, name: str) -> int:
        with self._factory() as db:
            user = db.query(User).filter(User.name == name).first()
            if user is None:
                user = User(name=name)
                db.add(user)
                db.commit()
                db.refresh(user)
            return user.id

    def create_session(self, user_id: int) -> int:
        with self._factory() as db:
            session = Session(user_id=user_id)
            db.add(session)
            db.commit()
            db.refresh(session)
            return session.id

    def list_sessions(self, user_id: int) -> List[dict]:
        with self._factory() as db:
            rows = (db.query(Session).filter(Session.user_id == user_id)
                    .order_by(Session.created_at.desc()).limit(50).all())
            return [{"id": s.id, "created_at": str(s.created_at)} for s in rows]

    def clear_history(self, user_id: int, keep_session_id: Optional[int] = None) -> int:
        """Delete the user's tasks, tool logs, messages and sessions.

        `keep_session_id` preserves the live session's messages so the agent
        keeps its conversation context; that session's tasks are still wiped.
        Returns the number of deleted rows.
        """
        with self._factory() as db:
            session_ids = [sid for (sid,) in
                           db.query(Session.id).filter(Session.user_id == user_id)]
            if not session_ids:
                return 0
            deleted = 0
            task_ids = [tid for (tid,) in
                        db.query(Task.id).filter(Task.session_id.in_(session_ids))]
            if task_ids:
                deleted += (db.query(ToolLog).filter(ToolLog.task_id.in_(task_ids))
                            .delete(synchronize_session=False))
                deleted += (db.query(Task).filter(Task.id.in_(task_ids))
                            .delete(synchronize_session=False))
            drop_ids = [sid for sid in session_ids if sid != keep_session_id]
            if drop_ids:
                deleted += (db.query(Message).filter(Message.session_id.in_(drop_ids))
                            .delete(synchronize_session=False))
                deleted += (db.query(Session).filter(Session.id.in_(drop_ids))
                            .delete(synchronize_session=False))
            db.commit()
            return deleted

    # --- conversation -----------------------------------------------------

    def add_message(self, session_id: int, role: str, content: str) -> None:
        with self._factory() as db:
            db.add(Message(session_id=session_id, role=role, content=content))
            db.commit()

    def get_recent_messages(self, session_id: int, limit: int = 10) -> List[Tuple[str, str]]:
        with self._factory() as db:
            rows = (db.query(Message).filter(Message.session_id == session_id)
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(limit).all())
            return [(m.role, m.content) for m in reversed(rows)]

    def list_messages(self, session_id: int) -> List[dict]:
        with self._factory() as db:
            rows = (db.query(Message).filter(Message.session_id == session_id)
                    .order_by(Message.id).all())
            return [{"id": m.id, "role": m.role, "content": m.content,
                     "created_at": str(m.created_at)} for m in rows]

    # --- tasks ------------------------------------------------------------

    def create_task(self, session_id: int, goal: str) -> int:
        with self._factory() as db:
            task = Task(session_id=session_id, goal=goal, status="in_progress")
            db.add(task)
            db.commit()
            db.refresh(task)
            return task.id

    def set_task_status(self, task_id: int, status: str) -> None:
        with self._factory() as db:
            task = db.get(Task, task_id)
            if task is not None:
                task.status = status
                db.commit()

    def list_tasks(self, session_id: Optional[int] = None, user_id: Optional[int] = None) -> List[dict]:
        with self._factory() as db:
            query = db.query(Task)
            if session_id is not None:
                query = query.filter(Task.session_id == session_id)
            elif user_id is not None:
                query = (query.join(Session, Task.session_id == Session.id)
                         .filter(Session.user_id == user_id))
            rows = query.order_by(Task.created_at.desc(), Task.id.desc()).limit(50).all()
            return [{"id": t.id, "session_id": t.session_id, "goal": t.goal,
                     "status": t.status, "created_at": str(t.created_at)} for t in rows]

    # --- tool logs ----------------------------------------------------------

    def log_tool(self, task_id: int, tool: str, args: dict, ok: bool,
                 result: object = None, error: Optional[str] = None) -> None:
        payload = {"ok": ok, "result": result, "error": error}
        with self._factory() as db:
            db.add(ToolLog(task_id=task_id, tool_name=tool,
                           arguments=json.dumps(args, default=str),
                           result=json.dumps(payload, default=str)[:4000]))
            db.commit()

    def list_tool_logs(self, task_id: int) -> List[dict]:
        with self._factory() as db:
            rows = (db.query(ToolLog).filter(ToolLog.task_id == task_id)
                    .order_by(ToolLog.id).all())
            return [{"id": l.id, "tool": l.tool_name, "arguments": l.arguments,
                     "result": l.result, "created_at": str(l.created_at)} for l in rows]
