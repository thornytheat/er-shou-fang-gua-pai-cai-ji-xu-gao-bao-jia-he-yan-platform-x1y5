"""行情版本控制器：发布 / 撤回 / 对比 / 回读。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import audit_service, market_version_service


def list_versions(db: Session, *, include_draft: bool) -> dict:
    payload = market_version_service.list_versions(db, include_draft=include_draft)
    # 草稿失效重算是把缓存写回库，GET 也需要提交
    db.commit()
    return payload


def draft_detail(db: Session) -> dict:
    draft, _stale = market_version_service.refresh_draft(db)
    db.commit()
    return market_version_service.serialize_detail(draft)


def publish(db: Session, principal: dict, request: Request, *, title: str | None, note: str | None) -> dict:
    version = market_version_service.publish(db, actor_email=principal["email"], title=title, note=note)
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="market.publish",
        entity="market_version",
        entity_id=version.version_no,
        detail={"rows": version.row_count, "flags": version.flag_counts, "csv_hash": version.csv_hash[:12]},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return market_version_service.serialize_detail(version)


def retract(db: Session, version_id: int, principal: dict, request: Request, *, note: str | None) -> dict:
    version = market_version_service.retract(db, version_id, actor_email=principal["email"], note=note)
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="market.retract",
        entity="market_version",
        entity_id=version.version_no,
        detail={"note": note},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return market_version_service.serialize_summary(version)


def detail(db: Session, version_id: int) -> dict:
    return market_version_service.serialize_detail(market_version_service.get_version(db, version_id))


def compare(db: Session, from_id: int, to_id: int) -> dict:
    return market_version_service.compare(db, from_id, to_id)


def download_csv(db: Session, version_id: int, principal: dict, request: Request) -> tuple[str, str, int, str]:
    version = market_version_service.get_version(db, version_id)
    content = market_version_service.csv_for_snapshot(version.snapshot or {})
    filename = f"rentlens-market-{version.version_no}.csv"
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="market.csv",
        entity="market_version",
        entity_id=version.version_no,
        detail={"rows": version.row_count, "csv_hash": version.csv_hash[:12]},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return content, filename, version.row_count, version.csv_hash
