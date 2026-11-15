"""导出控制器：CSV 响应 + 审计。"""
from __future__ import annotations

from fastapi import Request, Response
from sqlalchemy.orm import Session

from src.services import audit_service, export_service
from src.utils.csv_export import csv_response_headers


def _respond(db: Session, content: bytes, filename: str, action: str, principal: dict, request: Request) -> Response:
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action=action,
        entity="export",
        entity_id=filename,
        detail={"bytes": len(content)},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers=csv_response_headers(filename),
    )


def merge_runs_csv(db: Session, principal: dict, request: Request) -> Response:
    return _respond(db, export_service.merge_runs_csv(db), "merge-runs.csv", "export.merge_runs", principal, request)


def rejects_csv(db: Session, run_id: int | None, principal: dict, request: Request) -> Response:
    return _respond(db, export_service.rejects_csv(db, run_id), "rejects.csv", "export.rejects", principal, request)


def schema_changes_csv(db: Session, table_id: int | None, principal: dict, request: Request) -> Response:
    return _respond(
        db, export_service.schema_changes_csv(db, table_id), "schema-changes.csv", "export.schema_changes", principal, request
    )
