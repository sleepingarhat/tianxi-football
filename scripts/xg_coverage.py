"""xG 對接核對 — 檢查 data/xg 逐場能否對上 data/results 嘅賽果紀錄。

Understat 用 UTC 開賽時間，football-data.co.uk 用英國本地日期，所以夜場會差一日；
對名之後仍要容許 ±1 日嘅日期容差。本腳本同時輸出容差前後嘅覆蓋率，
寫入 snapshots/xg_coverage.json，覆蓋率低於門檻即 exit 1（供工作流告警）。

用法：python3 scripts/xg_coverage.py [--min 0.985]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import os

RESULTS = "data/results"
XG = "data/xg"
OUT = "snapshots/xg_coverage.json"


def results_index() -> dict[tuple[str, str, str], set[str]]:
    idx: dict[tuple[str, str, str], set[str]] = {}
    for fn in glob.glob(os.path.join(RESULTS, "*.csv")):
        div = os.path.basename(fn).split("_", 1)[1][:-4]
        with open(fn, encoding="utf-8", errors="replace") as f:
            for r in csv.DictReader(f):
                raw = (r.get("Date") or "").strip()
                p = raw.split("/")
                if len(p) != 3:
                    continue
                y = p[2]
                y = f"20{y}" if len(y) == 2 else y
                home = (r.get("HomeTeam") or "").strip()
                away = (r.get("AwayTeam") or "").strip()
                if not home or not away:
                    continue
                key = (div, home, away)
                idx.setdefault(key, set()).add(f"{y}-{p[1].zfill(2)}-{p[0].zfill(2)}")
    return idx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, default=0.985)
    args = ap.parse_args()

    idx = results_index()
    total = exact = tol = 0
    per_league: dict[str, dict[str, int]] = {}
    misses: list[str] = []

    for fn in sorted(glob.glob(os.path.join(XG, "*.csv"))):
        for r in csv.DictReader(open(fn, encoding="utf-8")):
            total += 1
            lg = r["league"]
            st = per_league.setdefault(lg, {"total": 0, "exact": 0, "tol": 0})
            st["total"] += 1
            dates = idx.get((r["div"], r["home"], r["away"]), set())
            d = r["date"]
            if d in dates:
                exact += 1
                st["exact"] += 1
                tol += 1
                st["tol"] += 1
                continue
            day = dt.date.fromisoformat(d)
            near = {(day + dt.timedelta(days=k)).isoformat() for k in (-1, 1)}
            if dates & near:
                tol += 1
                st["tol"] += 1
            elif len(misses) < 25:
                misses.append(r["match_key"])

    rate = tol / total if total else 0.0
    snap = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "matches": total,
        "exact_join": exact,
        "join_with_1day_tolerance": tol,
        "coverage_exact": round(exact / total, 6) if total else 0,
        "coverage_tolerant": round(rate, 6),
        "threshold": args.min,
        "pass": rate >= args.min,
        "per_league": {
            k: {
                **v,
                "coverage_tolerant": round(v["tol"] / v["total"], 6) if v["total"] else 0,
            }
            for k, v in sorted(per_league.items())
        },
        "sample_misses": misses,
        "note": "Understat 用 UTC，football-data 用英國本地日期，夜場差一日；引擎聯結時必須容許 ±1 日。",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")

    print(f"xG {total} 場：精準對接 {exact / total:.2%}，容差 ±1 日 {rate:.2%}（門檻 {args.min:.1%}）")
    for k, v in snap["per_league"].items():
        print(f"  {k}: {v['total']} 場 → {v['coverage_tolerant']:.2%}")
    return 0 if rate >= args.min else 1


if __name__ == "__main__":
    raise SystemExit(main())
