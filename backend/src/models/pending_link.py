"""跨表关联门禁：待关联 / 已放行 / 已撤回的从表记录。

自然键 (table_id, pk_value)：同一张从表的同一条主键只保留一行状态，
重复执行同一批事件只更新状态，不新增记录。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class PendingLink(Base):
    __tablename__ = "pending_links"
    __table_args__ = (
        UniqueConstraint("table_id", "pk_value", name="uq_pending_links_table_pk"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("source_tables.id", ondelete="CASCADE"), index=True)
    pk_value: Mapped[str] = mapped_column(String(80), index=True)
    event_lsn: Mapped[int] = mapped_column(Integer, default=0)
    event_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    op: Mapped[str] = mapped_column(String(4), default="u")
    parent_table_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_tables.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_table_code: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    parent_pk: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    parent_key_column: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reason: Mapped[str] = mapped_column(String(28), default="parent_missing", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    parent_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("merge_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    released_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("merge_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    release_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    withdraw_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
