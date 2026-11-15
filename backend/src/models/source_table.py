"""源表与订阅：一张被 CDC 订阅的上游业务表（MySQL binlog -> ODS）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class SourceTable(Base):
    __tablename__ = "source_tables"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    database_name: Mapped[str] = mapped_column(String(80), default="biz_prod")
    table_name: Mapped[str] = mapped_column(String(80))
    pk_column: Mapped[str] = mapped_column(String(60), default="id")
    # schema_columns: [{"name","type","nullable","default"}]
    schema_columns: Mapped[list] = mapped_column(JSON, default=list)
    binlog_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    binlog_position: Mapped[int] = mapped_column(Integer, default=0)
    allowed_lateness_seconds: Mapped[int] = mapped_column(Integer, default=30)
    owner: Mapped[str] = mapped_column(String(80), default="衡远保险 · 数据集成组")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 跨表关联门禁：本表为从表时，指向父表的编码 / 外键列 / 父单状态列 / 视为作废的状态值
    parent_table_code: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    parent_key_column: Mapped[str | None] = mapped_column(String(60), nullable=True)
    parent_status_column: Mapped[str | None] = mapped_column(String(60), nullable=True, default="status")
    void_statuses: Mapped[list] = mapped_column(JSON, default=list)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
