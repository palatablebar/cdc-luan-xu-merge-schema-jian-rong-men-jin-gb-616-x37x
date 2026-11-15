"""拒绝行：合并过程中被判定不可应用的事件，需要人工处置或重放。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class RejectRow(Base):
    __tablename__ = "reject_rows"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "table_id", "pk_value", "event_lsn", "reason", name="uq_reject_rows_natural_key"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("merge_runs.id", ondelete="CASCADE"), index=True)
    table_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_tables.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pk_value: Mapped[str] = mapped_column(String(80), index=True)
    op: Mapped[str] = mapped_column(String(4), default="u")
    reason: Mapped[str] = mapped_column(String(28), index=True)
    event_lsn: Mapped[int] = mapped_column(Integer, default=0)
    event_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    replay_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    replay_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
