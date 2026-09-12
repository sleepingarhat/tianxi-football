#!/usr/bin/env python3
"""FootyStats API 驗收腳本（採購前逐項對特徵字典核對）

用法：
  FOOTYSTATS_KEY=xxx python3 footystats_acceptance.py --league-id 12325 --outdir reports

驗收原則（與 docs/football-feature-dict.md 一致）：
  1. 賽前可得性：任何欄位若只在完場後出現，一律標 post_match（不可入模）。
  2. 歷史深度：逐聯賽報可用賽季數，少於 8 季者只作輔助源。
  3. xG 規格清單：逐球 xG＋座標、npxG、xGChain/xGBuildup、PPDA、deep completions、
     situation/shotType/lastAction、分鐘區間與位置拆分 —— 逐項 present/absent。
  4. 賠率欄只作離線對照軌記錄，market_beta = 0，永不入排名。
  5. 速率上限與重設時間由回應 header/body 讀出並記錄，唔靠猜。
輸出：reports/footystats_acceptance.json（機讀）＋ 終端摘要表。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

BASE = "https://api.football-data-api.com"

# 特徵字典要求的欄位 → 在 API 回應中預期出現的 key（逐項核對）
XG_CHECKS = {
    "team_xg": ["xg", "team_a_xg", "team_b_xg", "xg_for_avg_overall"],
    "npxg": ["npxg", "np_xg", "npxg_for_avg_overall"],
    "shot_level_xg": ["shots", "shotsxg", "shot_map", "xg_shot"],
    "shot_coordinates": ["x", "y", "coordinates", "shot_x"],
    "shot_context": ["situation", "shotType", "lastAction", "shot_type"],
    "xgchain_buildup": ["xgchain", "xgbuildup", "xg_chain"],
    "ppda": ["ppda", "ppda_against"],
    "deep_completions": ["deep", "deep_completions", "deep_allowed"],
    "minute_buckets": ["goals_scored_min_0_to_15", "goal_timings_recorded", "gs_0_15"],
    "position_split": ["position", "detailed", "player_position"],
}

PREMATCH_REQUIRED = [
    "date_unix", "homeID", "awayID", "competition_id", "season",
    "home_ppg", "away_ppg", "odds_ft_1", "odds_ft_x", "odds_ft_2",
]
POST_MATCH_ONLY = [
    "homeGoalCount", "awayGoalCount", "team_a_possession", "team_b_possession",
    "team_a_shots", "team_b_shots", "team_a_xg", "team_b_xg",
]


def get(path: str, key: str, **params) -> tuple[dict, dict]:
    url = f"{BASE}{path}?" + urlencode({"key": key, **params})
    with urlopen(url, timeout=60) as r:
        body = json.loads(r.read().decode("utf-8"))
        headers = {k.lower(): v for k, v in r.headers.items()}
    return body, headers


def walk_keys(obj, out: set[str], depth: int = 0) -> set[str]:
    if depth > 6:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k).lower())
            walk_keys(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:5]:
            walk_keys(v, out, depth + 1)
    return out


def check(present: set[str], wanted: list[str]) -> dict:
    hits = [w for w in wanted if w.lower() in present]
    return {"status": "present" if hits else "absent", "matched": hits}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-id", type=int, default=0, help="league_id（付費帳戶已勾選的聯賽）")
    ap.add_argument("--outdir", default="reports")
    ap.add_argument("--key", default=os.environ.get("FOOTYSTATS_KEY", "example"))
    args = ap.parse_args()

    report: dict = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "key_mode": "test" if args.key == "example" else "account",
                    "market_beta": 0, "endpoints": {}, "xg_spec": {}, "notes": []}

    # 1) 聯賽清單 + 歷史深度
    try:
        body, headers = get("/league-list", args.key, chosen_leagues_only="true")
        leagues = body.get("data", []) or []
        report["endpoints"]["league-list"] = {"ok": True, "count": len(leagues)}
        report["rate_limit"] = {k: v for k, v in headers.items() if "rate" in k or "limit" in k}
        depth = {}
        for lg in leagues:
            name = f"{lg.get('country','?')} {lg.get('league_name') or lg.get('name','?')}"
            depth[name] = len(lg.get("season", []) or [])
        report["season_depth"] = dict(sorted(depth.items(), key=lambda kv: -kv[1])[:40])
        report["notes"].append("歷史深度 < 8 季者只作輔助源，不作主源")
    except Exception as exc:  # noqa: BLE001
        report["endpoints"]["league-list"] = {"ok": False, "error": str(exc)}

    # 2) 賽事明細（賽前欄位 / 賽後欄位 / xG 規格）
    keys_seen: set[str] = set()
    for path, params in [("/todays-matches", {}),
                         ("/league-matches", {"season_id": args.league_id} if args.league_id else None),
                         ("/league-teams", {"season_id": args.league_id, "include": "stats"} if args.league_id else None)]:
        if params is None:
            report["endpoints"][path] = {"ok": False, "error": "需要 --league-id（付費帳戶勾選聯賽）"}
            continue
        try:
            body, _ = get(path, args.key, **params)
            data = body.get("data")
            keys_seen |= walk_keys(data, set())
            report["endpoints"][path] = {"ok": True,
                                         "records": len(data) if isinstance(data, list) else 1}
        except Exception as exc:  # noqa: BLE001
            report["endpoints"][path] = {"ok": False, "error": str(exc)}

    report["xg_spec"] = {name: check(keys_seen, wanted) for name, wanted in XG_CHECKS.items()}
    report["prematch_fields"] = check(keys_seen, PREMATCH_REQUIRED)
    report["post_match_only_fields_seen"] = check(keys_seen, POST_MATCH_ONLY)
    report["verdict"] = {
        "xg_present": sum(1 for v in report["xg_spec"].values() if v["status"] == "present"),
        "xg_total": len(XG_CHECKS),
        "recommend": "候選主 xG 源" if sum(
            1 for v in report["xg_spec"].values() if v["status"] == "present") >= 7 else "只作輔助／交叉核對源",
    }

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "footystats_acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
