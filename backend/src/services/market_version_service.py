"""行情版本：可发布、可回溯的行情快照。

口径：
  - 待发布（draft，全库一条）：每次读取时用当前库重算快照，内容指纹（input_hash）
    与库存不一致就自动失效重写——采集 / 改价 / 复核 / 重算之后无需手动触发。
  - 发布（publish）：把当下快照整体冻结成 V0001、V0002 …，主房源、可比样本、
    异常工单、判价依据（阈值常量 + 中位/MAD/z）全部入 snapshot，之后任何改写
    都不影响已发布版本；CSV 由冻结行重放，任何时间回读与当时导出逐字节一致。
  - 撤回（retract）：只允许撤回最新一版已发布，快照保留供回读与审计。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    FLAG_TYPE_LABELS,
    REVIEW_LABELS,
    VERSION_STATUS_LABELS,
    MarketVersionStatus,
)
from src.constants.limits import (
    AREA_TOL_SQM,
    CONFIDENCE_WEIGHTS,
    DEV_BORDER_PCT,
    DEV_FLAG_PCT,
    FLOOR_TOL,
    MIN_SAMPLES_FOR_FLAG,
    PRICE_TOL_PCT,
    SAMPLE_FULL,
    SIM_THRESHOLD,
    SIM_WEIGHTS,
    Z_FULL,
    Z_THRESHOLD,
)
from src.models.block_stat import BlockStat
from src.models.listing import Listing
from src.models.market_version import MarketVersion
from src.models.price_flag import PriceFlag
from src.services import export_service
from src.utils.errors import AppError
from src.utils.timeutil import iso_utc, utcnow

# 两版对比每类差异最多返回的条数（超出只给计数）
DIFF_LIMIT = 100

DRAFT_VERSION_NO = "DRAFT"


def rules_snapshot() -> dict:
    """判价依据：发布那一刻的算法口径常量（去重阈值 + 稳健 z 判定阈值）。"""
    return {
        "sim_threshold": SIM_THRESHOLD,
        "sim_weights": dict(SIM_WEIGHTS),
        "area_tol_sqm": AREA_TOL_SQM,
        "floor_tol": FLOOR_TOL,
        "price_tol_pct": PRICE_TOL_PCT,
        "min_samples": MIN_SAMPLES_FOR_FLAG,
        "z_threshold": Z_THRESHOLD,
        "dev_flag_pct": DEV_FLAG_PCT,
        "dev_border_pct": DEV_BORDER_PCT,
        "confidence_weights": dict(CONFIDENCE_WEIGHTS),
        "sample_full": SAMPLE_FULL,
        "z_full": Z_FULL,
    }


def _flag_basis(flag: PriceFlag, stat: BlockStat | None) -> str:
    mad_text = f"{stat.mad:.0f}" if stat and stat.mad else "0"
    z_text = f"{flag.z_score:.2f}" if flag.z_score is not None else "不可用（MAD=0）"
    return (
        f"样本 {flag.sample_size} 套（≥{MIN_SAMPLES_FOR_FLAG} 才判定）· 中位 {flag.median_price:.0f} · "
        f"MAD {mad_text} · z={z_text}（|z|>{Z_THRESHOLD:.0f}）· "
        f"偏离 {flag.deviation_pct:+.1f}%（|偏离|>{DEV_FLAG_PCT:.0f}% 判虚高/虚低，"
        f"{DEV_BORDER_PCT:.0f}%~{DEV_FLAG_PCT:.0f}% 边界进人工）"
    )


def build_snapshot(db: Session) -> dict:
    """用当前库重算一份完整快照：主房源明细 + 可比样本 + 异常工单 + 判价依据。"""
    items = export_service.collect_rows(db)
    stats = db.scalars(select(BlockStat).order_by(BlockStat.block_key)).all()

    # 可比样本：各小区×户型进入中位/MAD 的去重后主房源（面积缺失不进样本）
    primaries = db.scalars(select(Listing).where(Listing.is_primary.is_(True))).all()
    samples: dict[str, list[tuple[int, int]]] = {}
    for row in primaries:
        if row.area_sqm and row.unit_price:
            samples.setdefault(f"{row.community}::{row.layout}", []).append((row.unit_price, row.id))

    blocks: list[dict] = []
    for stat in stats:
        ordered = sorted(samples.get(stat.block_key, []))
        blocks.append(
            {
                "block_key": stat.block_key,
                "community": stat.community,
                "layout": stat.layout,
                "all_count": stat.all_count,
                "primary_count": stat.primary_count,
                "unknown_count": stat.unknown_count,
                "naive_mean": stat.naive_mean,
                "dedup_mean": stat.dedup_mean,
                "median_price": stat.median_price,
                "mad": stat.mad,
                "min_price": stat.min_price,
                "max_price": stat.max_price,
                "sample_ids": [listing_id for _, listing_id in ordered],
                "sample_prices": [price for price, _ in ordered],
            }
        )

    stat_by_key = {stat.block_key: stat for stat in stats}
    flags: list[dict] = []
    for flag in db.scalars(select(PriceFlag).order_by(PriceFlag.id)).all():
        flags.append(
            {
                "flag_id": flag.id,
                "listing_id": flag.listing_id,
                "community": flag.community,
                "layout": flag.layout,
                "flag_type": flag.flag_type,
                "flag_label": FLAG_TYPE_LABELS.get(flag.flag_type, flag.flag_type),
                "review_status": flag.review_status,
                "review_label": REVIEW_LABELS.get(flag.review_status, flag.review_status),
                "review_note": flag.review_note,
                "reviewed_by": flag.reviewed_by,
                "unit_price": flag.unit_price,
                "median_price": flag.median_price,
                "deviation_pct": flag.deviation_pct,
                "z_score": flag.z_score,
                "confidence": flag.confidence,
                "sample_size": flag.sample_size,
                "basis": _flag_basis(flag, stat_by_key.get(f"{flag.community}::{flag.layout}")),
            }
        )

    return {
        "generated_at": iso_utc(utcnow()),
        "rules": rules_snapshot(),
        "headers": list(export_service.HEADERS),
        "rows": [item["values"] for item in items],
        "row_keys": [item["key"] for item in items],
        "blocks": blocks,
        "flags": flags,
    }


def snapshot_hash(snapshot: dict) -> str:
    """快照内容指纹：不含 generated_at，同样输入同样哈希。"""
    payload = {key: value for key, value in snapshot.items() if key != "generated_at"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def csv_for_snapshot(snapshot: dict) -> str:
    """从冻结行重放 CSV：与发布当时导出的文件逐字节一致。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(snapshot["headers"])
    writer.writerows(snapshot["rows"])
    return buffer.getvalue()


def csv_hash_for(snapshot: dict) -> str:
    return hashlib.sha256(csv_for_snapshot(snapshot).encode("utf-8")).hexdigest()


def _flag_counts(snapshot: dict) -> dict:
    counts = {"high": 0, "low": 0, "uncertain": 0}
    for flag in snapshot["flags"]:
        counts[flag["flag_type"]] = counts.get(flag["flag_type"], 0) + 1
    return counts


def _write_draft(db: Session, snapshot: dict, content_hash: str) -> MarketVersion:
    draft = db.scalar(
        select(MarketVersion).where(MarketVersion.status == MarketVersionStatus.DRAFT.value)
    )
    if draft is None:
        draft = MarketVersion(
            version_no=DRAFT_VERSION_NO,
            seq=0,
            status=MarketVersionStatus.DRAFT.value,
            input_hash=content_hash,
        )
        db.add(draft)
    draft.snapshot = snapshot
    draft.input_hash = content_hash
    draft.row_count = len(snapshot["rows"])
    draft.flag_counts = _flag_counts(snapshot)
    draft.csv_hash = csv_hash_for(snapshot)
    draft.updated_at = utcnow()
    db.flush()
    return draft


def refresh_draft(db: Session) -> tuple[MarketVersion, bool]:
    """读取待发布草稿：样本或规则已变化（指纹不一致）就自动失效重算。"""
    snapshot = build_snapshot(db)
    content_hash = snapshot_hash(snapshot)
    draft = db.scalar(
        select(MarketVersion).where(MarketVersion.status == MarketVersionStatus.DRAFT.value)
    )
    stale = draft is None or draft.input_hash != content_hash
    if stale:
        draft = _write_draft(db, snapshot, content_hash)
    return draft, stale


def publish(db: Session, *, actor_email: str, title: str | None, note: str | None) -> MarketVersion:
    """发布：冻结当下快照为新版本，并把草稿对齐到同一内容。"""
    snapshot = build_snapshot(db)
    content_hash = snapshot_hash(snapshot)
    latest_seq = db.scalar(select(func.max(MarketVersion.seq)).where(MarketVersion.seq > 0)) or 0
    version = MarketVersion(
        version_no=f"V{latest_seq + 1:04d}",
        seq=latest_seq + 1,
        status=MarketVersionStatus.PUBLISHED.value,
        title=title or None,
        note=note or None,
        row_count=len(snapshot["rows"]),
        flag_counts=_flag_counts(snapshot),
        input_hash=content_hash,
        csv_hash=csv_hash_for(snapshot),
        snapshot=snapshot,
        published_by=actor_email,
        published_at=utcnow(),
    )
    db.add(version)
    db.flush()
    # 草稿与刚发布的版本对齐：再读草稿就是「与 V000x 一致」，而不是又算一遍
    _write_draft(db, snapshot, content_hash)
    return version


def latest_published(db: Session) -> MarketVersion | None:
    return db.scalar(
        select(MarketVersion)
        .where(MarketVersion.status == MarketVersionStatus.PUBLISHED.value)
        .order_by(MarketVersion.seq.desc())
        .limit(1)
    )


def retract(db: Session, version_id: int, *, actor_email: str, note: str | None) -> MarketVersion:
    """撤回上一版：只允许撤回当前最新的已发布版本，快照保留可回读。"""
    version = db.get(MarketVersion, version_id)
    if version is None or version.status == MarketVersionStatus.DRAFT.value:
        raise AppError("NOT_FOUND", 404, detail=f"version_id={version_id}")
    if version.status == MarketVersionStatus.RETRACTED.value:
        raise AppError("VERSION_STATE", 409, detail=f"{version.version_no} 已是撤回状态")
    latest = latest_published(db)
    if latest is None or latest.id != version.id:
        raise AppError(
            "VERSION_STATE",
            409,
            detail=f"只能撤回最新发布的一版（当前最新 {latest.version_no if latest else '无'}）",
        )
    version.status = MarketVersionStatus.RETRACTED.value
    version.retracted_by = actor_email
    version.retracted_at = utcnow()
    version.retract_note = note or None
    db.flush()
    return version


def get_version(db: Session, version_id: int) -> MarketVersion:
    version = db.get(MarketVersion, version_id)
    if version is None or version.status == MarketVersionStatus.DRAFT.value:
        raise AppError("NOT_FOUND", 404, detail=f"version_id={version_id}")
    return version


def _row_label(snapshot: dict, index: int) -> str:
    headers = snapshot["headers"]
    values = snapshot["rows"][index]
    community = values[headers.index("小区")] if "小区" in headers else ""
    layout = values[headers.index("户型")] if "户型" in headers else ""
    serial = values[headers.index("挂牌编号")] if "挂牌编号" in headers else ""
    return f"{community}·{layout} #{serial}"


def diff_snapshots(old: dict, new: dict, *, limit: int = DIFF_LIMIT) -> dict:
    """两版快照对比（纯函数）：主房源行、可比样本块、异常工单三口径。"""
    old_keys = {key["listing_id"]: i for i, key in enumerate(old["row_keys"])}
    new_keys = {key["listing_id"]: i for i, key in enumerate(new["row_keys"])}

    rows_added = [
        {"listing_id": lid, "label": _row_label(new, new_keys[lid])}
        for lid in sorted(new_keys.keys() - old_keys.keys())
    ]
    rows_removed = [
        {"listing_id": lid, "label": _row_label(old, old_keys[lid])}
        for lid in sorted(old_keys.keys() - new_keys.keys())
    ]
    rows_changed: list[dict] = []
    for lid in sorted(old_keys.keys() & new_keys.keys()):
        old_values = old["rows"][old_keys[lid]]
        new_values = new["rows"][new_keys[lid]]
        changes = [
            {"field": header, "from": old_values[pos], "to": new_values[pos]}
            for pos, header in enumerate(new["headers"])
            if pos < len(old_values) and old_values[pos] != new_values[pos]
        ]
        if changes:
            rows_changed.append(
                {"listing_id": lid, "label": _row_label(new, new_keys[lid]), "changes": changes}
            )

    old_blocks = {block["block_key"]: block for block in old["blocks"]}
    new_blocks = {block["block_key"]: block for block in new["blocks"]}
    block_fields = ["primary_count", "all_count", "naive_mean", "dedup_mean", "median_price", "mad"]
    blocks_changed: list[dict] = []
    for key in sorted(new_blocks.keys() | old_blocks.keys()):
        before = old_blocks.get(key)
        after = new_blocks.get(key)
        if before is None or after is None:
            blocks_changed.append(
                {
                    "block_key": key,
                    "community": (after or before)["community"],
                    "layout": (after or before)["layout"],
                    "changes": {"状态": ["无此块" if before is None else "有样本", "新增样本块" if before is None else "样本清空"]},
                }
            )
            continue
        changes = {
            field: [before.get(field), after.get(field)]
            for field in block_fields
            if before.get(field) != after.get(field)
        }
        if changes:
            blocks_changed.append(
                {"block_key": key, "community": after["community"], "layout": after["layout"], "changes": changes}
            )

    old_flags = {flag["listing_id"]: flag for flag in old["flags"]}
    new_flags = {flag["listing_id"]: flag for flag in new["flags"]}
    flags_added = [
        {"listing_id": lid, "flag_type": new_flags[lid]["flag_type"], "review_status": new_flags[lid]["review_status"]}
        for lid in sorted(new_flags.keys() - old_flags.keys())
    ]
    flags_removed = [
        {"listing_id": lid, "flag_type": old_flags[lid]["flag_type"], "review_status": old_flags[lid]["review_status"]}
        for lid in sorted(old_flags.keys() - new_flags.keys())
    ]
    flags_changed = [
        {
            "listing_id": lid,
            "from": {"flag_type": old_flags[lid]["flag_type"], "review_status": old_flags[lid]["review_status"]},
            "to": {"flag_type": new_flags[lid]["flag_type"], "review_status": new_flags[lid]["review_status"]},
        }
        for lid in sorted(old_flags.keys() & new_flags.keys())
        if (old_flags[lid]["flag_type"], old_flags[lid]["review_status"])
        != (new_flags[lid]["flag_type"], new_flags[lid]["review_status"])
    ]

    def capped(items: list[dict]) -> dict:
        return {"total": len(items), "truncated": len(items) > limit, "items": items[:limit]}

    return {
        "rows_added": capped(rows_added),
        "rows_removed": capped(rows_removed),
        "rows_changed": capped(rows_changed),
        "blocks_changed": capped(blocks_changed),
        "flags_added": capped(flags_added),
        "flags_removed": capped(flags_removed),
        "flags_changed": capped(flags_changed),
        "summary": {
            "rows_added": len(rows_added),
            "rows_removed": len(rows_removed),
            "rows_changed": len(rows_changed),
            "blocks_changed": len(blocks_changed),
            "flags_added": len(flags_added),
            "flags_removed": len(flags_removed),
            "flags_changed": len(flags_changed),
        },
    }


def serialize_summary(version: MarketVersion) -> dict:
    return {
        "id": version.id,
        "version_no": version.version_no,
        "seq": version.seq,
        "status": version.status,
        "status_label": VERSION_STATUS_LABELS.get(version.status, version.status),
        "title": version.title,
        "note": version.note,
        "row_count": version.row_count,
        "flag_counts": version.flag_counts or {},
        "input_hash": version.input_hash,
        "csv_hash": version.csv_hash,
        "published_by": version.published_by,
        "published_at": iso_utc(version.published_at),
        "retracted_by": version.retracted_by,
        "retracted_at": iso_utc(version.retracted_at),
        "retract_note": version.retract_note,
        "computed_at": iso_utc(version.updated_at),
    }


def serialize_detail(version: MarketVersion) -> dict:
    return {**serialize_summary(version), "snapshot": version.snapshot}


def list_versions(db: Session, *, include_draft: bool) -> dict:
    """发布历史 + （有权限时的）待发布草稿摘要与其距最新版的差异。"""
    items = db.scalars(
        select(MarketVersion)
        .where(MarketVersion.status != MarketVersionStatus.DRAFT.value)
        .order_by(MarketVersion.seq.desc())
    ).all()
    payload: dict = {
        "items": [serialize_summary(row) for row in items],
        "draft": None,
    }
    if include_draft:
        draft, stale = refresh_draft(db)
        latest = latest_published(db)
        vs_latest = None
        if latest is not None and latest.snapshot and draft.snapshot:
            vs_latest = diff_snapshots(latest.snapshot, draft.snapshot)["summary"]
        payload["draft"] = {
            "computed_at": iso_utc(draft.updated_at),
            "row_count": draft.row_count,
            "flag_counts": draft.flag_counts or {},
            "input_hash": draft.input_hash,
            "csv_hash": draft.csv_hash,
            "just_recomputed": stale,
            "latest_version_no": latest.version_no if latest else None,
            "vs_latest": vs_latest,
        }
    return payload


def compare(db: Session, from_id: int, to_id: int) -> dict:
    older = get_version(db, from_id)
    newer = get_version(db, to_id)
    if not older.snapshot or not newer.snapshot:
        raise AppError("VERSION_STATE", 409, detail="版本缺少快照，无法对比")
    return {
        "from": serialize_summary(older),
        "to": serialize_summary(newer),
        **diff_snapshots(older.snapshot, newer.snapshot),
    }
