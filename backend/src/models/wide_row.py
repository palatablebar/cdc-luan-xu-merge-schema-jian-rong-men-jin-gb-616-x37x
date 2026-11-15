"""宽表发布层：跨表合并后真正对外发布的宽表行（每个父表主键一行）。

发布口径
--------
- 只有「父单存在且未作废」的理赔记录才会出现在这里；
- source_run_id 记录最近一次写入该行的批次，重算可核对；
- 自然键 (parent_table_code, parent_pk)，重复执行同一批只更新不新增。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class WideRow(Base):
    __tablename__ = "wide_rows"
    __table_args__ = (
        UniqueConstraint("parent_table_code", "parent_pk", name="uq_wide_rows_parent"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    parent_table_code: Mapped[str] = mapped_column(String(40), index=True)
    parent_pk: Mapped[str] = mapped_column(String(80), index=True)
    parent_table_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_tables.id", ondelete="SET NULL"), nullable=True
    )
    child_table_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_tables.id", ondelete="SET NULL"), nullable=True, index=True
    )
    child_rows: Mapped[list] = mapped_column(JSON, default=list)
    parent_row: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="published", index=True)
    source_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("merge_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    link_basis: Mapped[str] = mapped_column(String(12), default="linked")
    metric: Mapped[dict] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
