from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON
from datetime import datetime

from app.db.session import Base


class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    priority = Column(Integer, default=0)
    status = Column(String, default="active")

    created_at = Column(DateTime, default=datetime.utcnow)


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    goal_id = Column(Integer, ForeignKey("goals.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    status = Column(String, default="active")

    created_at = Column(DateTime, default=datetime.utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    status = Column(String, default="pending")

    created_at = Column(DateTime, default=datetime.utcnow)


class Execution(Base):
    __tablename__ = "executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)

    intent = Column(String, nullable=True)
    tool = Column(String, nullable=True)
    tool_input = Column(JSON, default=dict)
    status = Column(String, nullable=False)
    result = Column(JSON, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
