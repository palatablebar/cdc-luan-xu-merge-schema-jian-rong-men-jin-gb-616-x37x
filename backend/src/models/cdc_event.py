"""CDC 事件：一行 binlog（含乱序标记）。自然键 (table_id, pk_value, lsn)。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class CdcEvent(Base):
    __tablename__ = "cdc_events"
    __table_args__ = (
        UniqueConstraint("table_id", "pk_value", "lsn", "seq", name="uq_cdc_events_table_pk_lsn_seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("source_tables.id", ondelete="CASCADE"), index=True)
    pk_value: Mapped[str] = mapped_column(String(80), index=True)
    lsn: Mapped[int] = mapped_column(Integer, index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0, index=True)
    op: Mapped[str] = mapped_column(String(4), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    ingest_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    before_data: Mapped[dict] = mapped_column(JSON, default=dict)
    after_data: Mapped[dict] = mapped_column(JSON, default=dict)
    is_late: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    late_seconds: Mapped[int] = mapped_column(Integer, default=0)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="applied", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
