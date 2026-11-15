from __future__ import annotations

from pydantic import BaseModel, Field


class ColumnDef(BaseModel):
    name: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    type: str = Field(min_length=1, max_length=40)
    nullable: bool = True
    default: str | None = None


class TableIn(BaseModel):
    code: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    name: str = Field(min_length=2, max_length=120)
    database_name: str = Field(default="biz_prod", max_length=80)
    table_name: str = Field(min_length=1, max_length=80)
    pk_column: str = Field(default="id", max_length=60)
    schema_columns: list[ColumnDef] = Field(default_factory=list, max_length=120)
    binlog_file: str | None = Field(default=None, max_length=120)
    binlog_position: int = Field(default=0, ge=0)
    allowed_lateness_seconds: int = Field(default=30, ge=0, le=3600)
    owner: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=2000)
    enabled: bool = True


class TablePatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    allowed_lateness_seconds: int | None = Field(default=None, ge=0, le=3600)
    binlog_file: str | None = Field(default=None, max_length=120)
    binlog_position: int | None = Field(default=None, ge=0)
    schema_columns: list[ColumnDef] | None = Field(default=None, max_length=120)
    owner: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
