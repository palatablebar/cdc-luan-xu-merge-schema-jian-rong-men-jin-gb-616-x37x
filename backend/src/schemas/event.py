from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EventIn(BaseModel):
    table_id: int = Field(gt=0)
    pk_value: str = Field(min_length=1, max_length=80)
    lsn: int = Field(gt=0)
    op: Literal["c", "u", "d"] = "u"
    event_time: datetime | None = None
    after: dict = Field(default_factory=dict)
    before: dict = Field(default_factory=dict)
