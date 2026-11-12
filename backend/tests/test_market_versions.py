"""行情版本的纯函数：快照指纹、CSV 重放、两版对比。

不连 MySQL / Redis / HTTP：快照直接用 dict 手工构造，
期望值全部手算；CSV 重放与 export_service 同一 writer 口径。
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.constants.limits import DEV_FLAG_PCT, MIN_SAMPLES_FOR_FLAG, SIM_THRESHOLD, Z_THRESHOLD  # noqa: E402
from src.services import market_version_service as mvs  # noqa: E402

HEADERS = ["小区", "户型", "挂牌编号", "单价(元/㎡)", "价格标记"]


def make_snapshot(
    rows: list[list],
    listing_ids: list[int],
    *,
    blocks: list[dict] | None = None,
    flags: list[dict] | None = None,
    generated_at: str = "2026-11-12T01:30:00Z",
) -> dict:
    return {
        "generated_at": generated_at,
        "rules": mvs.rules_snapshot(),
        "headers": list(HEADERS),
        "rows": rows,
        "row_keys": [
            {"listing_id": lid, "cluster_id": lid + 100, "flag_id": None, "block_key": "兰亭苑::三居"}
            for lid in listing_ids
        ],
        "blocks": blocks
        or [
            {
                "block_key": "兰亭苑::三居",
                "community": "兰亭苑",
                "layout": "三居",
                "all_count": 42,
                "primary_count": 28,
                "unknown_count": 1,
                "naive_mean": 61200.0,
                "dedup_mean": 58900.0,
                "median_price": 58000.0,
                "mad": 1200.0,
                "min_price": 51000.0,
                "max_price": 79072.0,
                "sample_ids": listing_ids,
                "sample_prices": [row[3] for row in rows],
            }
        ],
        "flags": flags
        or [
            {
                "flag_id": 9,
                "listing_id": listing_ids[0],
                "community": "兰亭苑",
                "layout": "三居",
                "flag_type": "high",
                "review_status": "pending",
                "unit_price": 79072,
                "median_price": 58000.0,
                "deviation_pct": 36.33,
                "z_score": 4.31,
                "confidence": 0.86,
                "sample_size": 28,
                "basis": "样本 28 套 · 中位 58000 · MAD 1200 · z=4.31",
            }
        ],
    }


BASE_ROWS = [
    ["兰亭苑", "三居", "LJ-001", 79072, "虚高"],
    ["兰亭苑", "三居", "LJ-002", 58000, ""],
    ["兰亭苑", "三居", "LJ-003", 56500, ""],
]


def test_snapshot_hash_ignores_generated_at():
    snap_a = make_snapshot(BASE_ROWS, [11, 12, 13], generated_at="2026-11-12T01:30:00Z")
    snap_b = make_snapshot(BASE_ROWS, [11, 12, 13], generated_at="2026-11-13T09:00:00Z")
    assert mvs.snapshot_hash(snap_a) == mvs.snapshot_hash(snap_b)


def test_snapshot_hash_changes_with_content():
    snap_a = make_snapshot(BASE_ROWS, [11, 12, 13])
    changed = [row[:] for row in BASE_ROWS]
    changed[0][3] = 63900  # 改价：79,072 -> 63,900
    snap_b = make_snapshot(changed, [11, 12, 13])
    assert mvs.snapshot_hash(snap_a) != mvs.snapshot_hash(snap_b)


def test_csv_replay_is_byte_identical():
    snapshot = make_snapshot(BASE_ROWS, [11, 12, 13])
    first = mvs.csv_for_snapshot(snapshot)
    second = mvs.csv_for_snapshot(snapshot)
    assert first == second
    # 与导出同一 writer 口径：头 + 3 行，\n 结尾
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADERS)
    writer.writerows(BASE_ROWS)
    assert first == buffer.getvalue()
    assert mvs.csv_hash_for(snapshot) == mvs.csv_hash_for(snapshot)


def test_rules_snapshot_matches_limits():
    rules = mvs.rules_snapshot()
    assert rules["min_samples"] == MIN_SAMPLES_FOR_FLAG
    assert rules["z_threshold"] == Z_THRESHOLD
    assert rules["dev_flag_pct"] == DEV_FLAG_PCT
    assert rules["sim_threshold"] == SIM_THRESHOLD


def test_diff_rows_added_removed_changed():
    old = make_snapshot(BASE_ROWS, [11, 12, 13])
    new_rows = [row[:] for row in BASE_ROWS]
    new_rows[0][3] = 63900  # listing 11 改价
    new_rows[0][4] = ""
    new_rows.append(["兰亭苑", "三居", "LJ-004", 60100, ""])  # 新增 listing 14
    new = make_snapshot(new_rows, [11, 12, 13, 14])
    # 去掉 listing 12：构造移除场景
    new["rows"] = [row for row in new["rows"] if row[2] != "LJ-002"]
    new["row_keys"] = [key for key in new["row_keys"] if key["listing_id"] != 12]

    diff = mvs.diff_snapshots(old, new)
    assert diff["summary"]["rows_added"] == 1
    assert diff["summary"]["rows_removed"] == 1
    assert diff["summary"]["rows_changed"] == 1

    changed = diff["rows_changed"]["items"][0]
    assert changed["listing_id"] == 11
    assert changed["label"] == "兰亭苑·三居 #LJ-001"
    assert {change["field"] for change in changed["changes"]} == {"单价(元/㎡)", "价格标记"}
    price_change = next(c for c in changed["changes"] if c["field"] == "单价(元/㎡)")
    assert price_change["from"] == 79072
    assert price_change["to"] == 63900

    assert diff["rows_added"]["items"][0]["label"] == "兰亭苑·三居 #LJ-004"
    assert diff["rows_removed"]["items"][0]["label"] == "兰亭苑·三居 #LJ-002"


def test_diff_blocks_and_flags():
    old = make_snapshot(BASE_ROWS, [11, 12, 13])
    new = make_snapshot(BASE_ROWS, [11, 12, 13])
    new["blocks"][0]["median_price"] = 57500.0
    new["blocks"][0]["primary_count"] = 29
    new["flags"][0]["review_status"] = "confirmed"
    new["flags"].append(
        {
            "flag_id": 10,
            "listing_id": 12,
            "community": "兰亭苑",
            "layout": "三居",
            "flag_type": "low",
            "review_status": "pending",
            "unit_price": 40000,
            "median_price": 57500.0,
            "deviation_pct": -30.43,
            "z_score": -3.5,
            "confidence": 0.7,
            "sample_size": 29,
            "basis": "…",
        }
    )

    diff = mvs.diff_snapshots(old, new)
    assert diff["summary"]["blocks_changed"] == 1
    block = diff["blocks_changed"]["items"][0]
    assert block["block_key"] == "兰亭苑::三居"
    assert block["changes"]["median_price"] == [58000.0, 57500.0]
    assert block["changes"]["primary_count"] == [28, 29]

    assert diff["summary"]["flags_added"] == 1
    assert diff["flags_added"]["items"][0]["listing_id"] == 12
    assert diff["summary"]["flags_changed"] == 1
    flag_change = diff["flags_changed"]["items"][0]
    assert flag_change["from"]["review_status"] == "pending"
    assert flag_change["to"]["review_status"] == "confirmed"
    assert diff["summary"]["flags_removed"] == 0


def test_diff_identical_snapshots_is_empty():
    old = make_snapshot(BASE_ROWS, [11, 12, 13])
    new = make_snapshot(BASE_ROWS, [11, 12, 13], generated_at="2026-11-13T09:00:00Z")
    diff = mvs.diff_snapshots(old, new)
    assert all(count == 0 for count in diff["summary"].values())
