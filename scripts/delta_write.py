#!/usr/bin/env python3
"""場次條件層 Δλ 只附加寫入器（S27）。

硬規則：
- 只 append，永不 UPDATE／刪行；修正寫新一行並填 supersedes。
- 唔准觸碰 data/predictions/、models/、snapshots/ 或任何指紋欄。
- 現階段結構落地：delta_h/delta_a 一律 0、applied 一律 False。
- 缺資料 → status="missing"、Δλ=0，場次退回基準 λ 並標紅燈。
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone

TABLES = ("delta_squad", "delta_density", "delta_market")
SCHEMA_VERSION = 1
FORBIDDEN_PREFIXES = ("data/predictions", "models", "snapshots")
FROZEN_KEYS = {"p", "lambda", "cs", "fingerprint", "locked_at", "result"}

COMMON = {
    "record_id",
    "match_key",
    "div",
    "kickoff_utc",
    "written_at",
    "source",
    "source_ts",
    "status",
    "delta_h",
    "delta_a",
    "applied",
    "supersedes",
    "notes",
}

EXTRA = {
    "delta_squad": {
        "lineup_published_ts",
        "missing_starters_h",
        "missing_starters_a",
        "minutes_weight_h",
        "minutes_weight_a",
    },
    "delta_density": {
        "rest_days_h",
        "rest_days_a",
        "matches_14d_h",
        "matches_14d_a",
        "travel_km_h",
        "travel_km_a",
    },
    "delta_market": {
        "novig_p",
        "model_p",
        "resid",
        "book_count",
        "captured_before_lock",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _guard(path: str) -> None:
    norm = path.replace("\\", "/").lstrip("./")
    for bad in FORBIDDEN_PREFIXES:
        if norm.startswith(bad):
            raise SystemExit(f"拒絕：Δλ 寫入器唔准觸碰凍結路徑 {bad}/")


def build(table: str, payload: dict) -> dict:
    if table not in TABLES:
        raise SystemExit(f"未知表：{table}")
    bad = FROZEN_KEYS & set(payload)
    if bad:
        raise SystemExit(f"拒絕：Δλ 紀錄唔准帶凍結欄 {sorted(bad)}")

    allowed = COMMON | EXTRA[table]
    unknown = set(payload) - allowed
    if unknown:
        raise SystemExit(f"未知欄位：{sorted(unknown)}")

    row = {k: None for k in sorted(allowed)}
    row.update(payload)
    row["record_id"] = payload.get("record_id") or str(uuid.uuid4())
    row["written_at"] = _now()
    row["status"] = payload.get("status") or "missing"
    # 結構階段：一律唔估 λ、唔入生成
    row["delta_h"] = 0.0
    row["delta_a"] = 0.0
    row["applied"] = False
    if row["status"] != "ok":
        row["notes"] = row.get("notes") or "缺資料，Δλ=0，退回基準 λ，場次標紅燈"
    return row


def append(out_dir: str, table: str, rows: list[dict]) -> int:
    _guard(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    ver = os.path.join(out_dir, "_schema_version")
    if not os.path.exists(ver):
        with open(ver, "w", encoding="utf-8") as f:
            f.write(f"{SCHEMA_VERSION}\n")
    path = os.path.join(out_dir, f"{table}.jsonl")
    with open(path, "a", encoding="utf-8") as f:  # append-only
        for r in rows:
            f.write(json.dumps(build(table, r), ensure_ascii=False) + "\n")
    return len(rows)


def init(out_dir: str) -> None:
    _guard(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "_schema_version"), "w", encoding="utf-8") as f:
        f.write(f"{SCHEMA_VERSION}\n")
    for t in TABLES:
        p = os.path.join(out_dir, f"{t}.jsonl")
        if not os.path.exists(p):
            open(p, "a", encoding="utf-8").close()
        print(f"ready {p}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/delta")
    ap.add_argument("--init", action="store_true", help="只建空表同結構版本")
    ap.add_argument("--table", choices=TABLES)
    ap.add_argument("--json", help="一條或一個 list 嘅 JSON 紀錄")
    a = ap.parse_args()

    if a.init or not a.table:
        init(a.out)
        return

    payload = json.loads(a.json or "{}")
    rows = payload if isinstance(payload, list) else [payload]
    n = append(a.out, a.table, rows)
    print(f"appended {n} row(s) to {a.table}.jsonl")


if __name__ == "__main__":
    main()
