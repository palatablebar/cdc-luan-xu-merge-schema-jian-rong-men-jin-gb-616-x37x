"""ORM 模型聚合导出（Base 用于 create_all）。"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from src.models.user import User  # noqa: E402
from src.models.audit import AuditLog  # noqa: E402
from src.models.source_table import SourceTable  # noqa: E402
from src.models.cdc_event import CdcEvent  # noqa: E402
from src.models.merge_run import MergeRun  # noqa: E402
from src.models.reject_row import RejectRow  # noqa: E402
from src.models.schema_change import SchemaChange  # noqa: E402

__all__ = ["Base", "User", "AuditLog", "SourceTable", "CdcEvent", "MergeRun", "RejectRow", "SchemaChange"]
