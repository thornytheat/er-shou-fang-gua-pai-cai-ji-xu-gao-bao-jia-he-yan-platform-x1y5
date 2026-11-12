from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.constants.roles import EXPORT_ROLES, MARKET_DRAFT_ROLES, READ_ROLES
from src.controllers import market_version_controller
from src.middlewares.rbac import require_roles

router = APIRouter(prefix="/market-versions", tags=["market-versions"])


class PublishIn(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=500)


class RetractIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.get("")
def list_versions(
    principal: dict = Depends(require_roles(*READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    include_draft = principal["role"] in MARKET_DRAFT_ROLES
    return market_version_controller.list_versions(db, include_draft=include_draft)


@router.get("/draft")
def draft(
    principal: dict = Depends(require_roles(*MARKET_DRAFT_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return market_version_controller.draft_detail(db)


@router.get("/compare")
def compare(
    from_id: int,
    to_id: int,
    principal: dict = Depends(require_roles(*READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return market_version_controller.compare(db, from_id, to_id)


@router.post("/publish")
def publish(
    payload: PublishIn,
    request: Request,
    principal: dict = Depends(require_roles(*EXPORT_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return market_version_controller.publish(
        db, principal, request, title=payload.title, note=payload.note
    )


@router.get("/{version_id}")
def detail(
    version_id: int,
    principal: dict = Depends(require_roles(*READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return market_version_controller.detail(db, version_id)


@router.get("/{version_id}/csv")
def download_csv(
    version_id: int,
    request: Request,
    principal: dict = Depends(require_roles(*EXPORT_ROLES)),
    db: Session = Depends(get_db),
) -> Response:
    content, filename, row_count, csv_hash = market_version_controller.download_csv(
        db, version_id, principal, request
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Rentlens-Rows": str(row_count),
            "X-Rentlens-Sha256": csv_hash,
        },
    )


@router.post("/{version_id}/retract")
def retract(
    version_id: int,
    payload: RetractIn,
    request: Request,
    principal: dict = Depends(require_roles(*EXPORT_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return market_version_controller.retract(db, version_id, principal, request, note=payload.note)
