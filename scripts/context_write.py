#!/usr/bin/env python3
"""條件層資料只附加寫入器（S29）。

只記事實：公布名單時間戳、穩定分鐘、賽前 projected xG、門將撲救。
硬規則：
- 只 append，永不 UPDATE／刪行；修正寫新一行並填 supersedes。
- 唔准觸碰 data/predictions/、models/、snapshots/，唔准帶凍結欄。
- 唔生成 δ、唔估 λ：eligible 只係「將來准入」嘅閘，唔係調整量。
- 缺資料 → status=missing、eligible=false；唔准用平均／上仗值頂替。
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone

TABLES = ("lineups", "player_minutes", "projected_xg", "gk_saves")
SCHEMA_VERSION = 1
LOCK_MINUTES = 60
MIN_WINDOW_MATCHES = 5
FORBIDDEN_PREFIXES = ("data/predictions", "models", "snapshots")
FROZEN_KEYS = {"p", "lambda", "cs", "fingerprint", "locked_at", "result",
               "delta_h", "delta_a", "applied"}

COMMON = {
    "record_id", "match_key", "div", "kickoff_utc", "written_at",
    "source", "source_ts", "status", "eligible", "supersedes", "notes",
}

EXTRA = {
    "lineups": {
        "team_h", "team_a", "source_kind",
        "lineup_published_ts", "lineup_observed_ts", "effective_ts",
        "lead_minutes", "starters_h", "starters_a", "complete_h", "complete_a",
    },
    "player_minutes": {
        "player_id", "player_name", "team",
        "window_matches", "minutes_total", "minutes_share", "window_end",
    },
    "projected_xg": {"xg_h", "xg_a", "is_pre_match", "model_tag"},
    "gk_saves": {
        "player_id", "player_name", "team",
        "window_matches", "shots_faced", "saves", "save_rate", "window_end",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _guard(path: str) -> None:
    norm = path.replace("\\", "/").lstrip("./")
    for bad in FORBIDDEN_PREFIXES:
        if norm.startswith(bad):
            raise SystemExit(f"拒絕：條件層寫入器唔准觸碰凍結路徑 {bad}/")


def _decide_lineup(row: dict) -> dict:
    ko = _parse(row.get("kickoff_utc"))
    eff = _parse(row.get("lineup_published_ts")) or _parse(row.get("lineup_observed_ts"))
    row["effective_ts"] = eff.isoformat(timespec="seconds") if eff else None
    complete = bool(row.get("complete_h")) and bool(row.get("complete_a"))

    if eff is None or ko is None:
        row["lead_minutes"] = None
        row["status"] = row.get("status") or "missing"
        row["eligible"] = False
        return row

    lead = (ko - eff).total_seconds() / 60.0
    row["lead_minutes"] = round(lead, 1)
    if lead < 0:
        row["status"], row["eligible"] = "post_kickoff", False
    elif lead < LOCK_MINUTES:
        row["status"], row["eligible"] = "late", False
    elif not complete:
        row["status"], row["eligible"] = "incomplete", False
    else:
        row["status"], row["eligible"] = "ok", True
    return row


def _decide_window(row: dict) -> dict:
    ko = _parse(row.get("kickoff_utc"))
    end = _parse(row.get("window_end"))
    n = row.get("window_matches") or 0
    ok = end is not None and n >= MIN_WINDOW_MATCHES and (ko is None or end < ko)
    row["status"] = "ok" if ok else (row.get("status") or "missing")
    row["eligible"] = bool(ok)
    return row


def _decide_xg(row: dict) -> dict:
    ko = _parse(row.get("kickoff_utc"))
    ts = _parse(row.get("source_ts"))
    pre = bool(row.get("is_pre_match"))
    ok = pre and ts is not None and ko is not None and (ko - ts).total_seconds() / 60.0 >= LOCK_MINUTES
    row["status"] = "ok" if ok else (row.get("status") or "missing")
    row["eligible"] = bool(ok)
    return row


def build(table: str, payload: dict) -> dict:
    if table not in TABLES:
        raise SystemExit(f"未知表：{table}")
    bad = FROZEN_KEYS & set(payload)
    if bad:
        raise SystemExit(f"拒絕：條件層紀錄唔准帶凍結／δ 欄 {sorted(bad)}")

    allowed = COMMON | EXTRA[table]
    unknown = set(payload) - allowed
    if unknown:
        raise SystemExit(f"未知欄位：{sorted(unknown)}")

    row = {k: None for k in sorted(allowed)}
    row.update(payload)
    row["record_id"] = payload.get("record_id") or str(uuid.uuid4())
    row["written_at"] = _now()

    if table == "lineups":
        row = _decide_lineup(row)
    elif table == "projected_xg":
        row = _decide_xg(row)
    else:
        row = _decide_window(row)

    if not row["eligible"] and not row.get("notes"):
        row["notes"] = "唔夠資格入 δ：Δλ=0，退回基準 λ，場次標紅燈"
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
    ap.add_argument("--out", default="data/context")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--table", choices=TABLES)
    ap.add_argument("--json")
    a = ap.parse_args()

    if a.init or not a.table:
        init(a.out)
        return
    payload = json.loads(a.json or "{}")
    rows = payload if isinstance(payload, list) else [payload]
    print(f"appended {append(a.out, a.table, rows)} row(s) to {a.table}.jsonl")


if __name__ == "__main__":
    main()
