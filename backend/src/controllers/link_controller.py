"""跨表关联门禁控制器。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import link_service


def overview(db: Session) -> dict:
    return link_service.overview(db)


def list_links(db: Session, **filters) -> dict:
    return link_service.list_links(db, **filters)


def get_link(db: Session, link_id: int) -> dict:
    return link_service.get_link(db, link_id)


def list_wide_rows(db: Session, **filters) -> dict:
    return link_service.list_wide_rows(db, **filters)


def recompute(db: Session, principal: dict, request: Request) -> dict:
    return link_service.recompute(db, principal=principal, request=request)
