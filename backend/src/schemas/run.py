from __future__ import annotations

from pydantic import BaseModel, Field


class RunIn(BaseModel):
    table_id: int | None = Field(default=None, gt=0)
    allowed_lateness_seconds: int | None = Field(default=None, ge=0, le=3600)
    trigger: str = Field(default="manual", max_length=20)
