from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GateIn(BaseModel):
    gate_status: Literal["pending", "approved", "blocked"]
    note: str | None = Field(default=None, max_length=1000)
