from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from core.database import Base
from sqlalchemy.orm import relationship

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    sessions = relationship("Session", back_populates="user")

class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="sessions")
    tasks = relationship("Task", back_populates="session")
    messages = relationship("Message", back_populates="session")

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"))
    goal = Column(Text)
    status = Column(String, default="pending") # pending, in_progress, completed, failed
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    session = relationship("Session", back_populates="tasks")
    logs = relationship("ToolLog", back_populates="task")

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"))
    role = Column(String) # user, agent, system
    content = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    session = relationship("Session", back_populates="messages")

class ToolLog(Base):
    __tablename__ = "tool_logs"
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"))
    tool_name = Column(String)
    arguments = Column(Text) # JSON string
    result = Column(Text) # JSON string
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    task = relationship("Task", back_populates="logs")
