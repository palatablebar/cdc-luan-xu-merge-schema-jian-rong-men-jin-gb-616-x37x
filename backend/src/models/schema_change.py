"""Schema 变更：上游 DDL 快照 + 算法 B 的兼容性结论与门禁状态。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class SchemaChange(Base):
    __tablename__ = "schema_changes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("source_tables.id", ondelete="CASCADE"), index=True)
    change_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    column_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    target_column: Mapped[str | None] = mapped_column(String(60), nullable=True)
    before_columns: Mapped[list] = mapped_column(JSON, default=list)
    after_columns: Mapped[list] = mapped_column(JSON, default=list)
    compat: Mapped[str] = mapped_column(String(16), index=True)
    severity: Mapped[str] = mapped_column(String(12), default="low", index=True)
    action: Mapped[str] = mapped_column(String(12), default="apply", index=True)
    rollback_sql: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    gate_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reviewer: Mapped[str | None] = mapped_column(String(160), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
