#!/usr/bin/env python3
"""傷停採集（S35 資料層，只記事實）。

來源：API-SPORTS /injuries。免費層限 2022–2024 季：
- 歷史季（2022–2024）：可以入庫，做將來 δ_名單 嘅回測材料。
- 當季：免費層一律回 plan 錯誤 → 只寫 status=missing 佔位行，
  唔用上季／平均／上仗值頂替（等於偷近況）。
唔生成 δ、唔估 λ、唔碰凍結路徑同指紋。
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

import context_write as cw

API_HOST = "https://v3.football.api-sports.io"
DIV_LEAGUE = {"E0": 39, "D1": 78, "SP1": 140, "I1": 135, "F1": 61}
FREE_SEASONS = (2022, 2023, 2024)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fetch(path: str, key: str) -> dict:
    req = urllib.request.Request(f"{API_HOST}/{path}", headers={"x-apisports-key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def collect(div: str, season: int, key: str | None) -> list[dict]:
    base = {"div": div, "season": season}
    if not key:
        return [{**base, "source": "none", "status": "missing",
                 "notes": "冇授權傷停來源 key：唔用替代值"}]
    if season not in FREE_SEASONS:
        return [{**base, "source": "api-sports", "status": "missing",
                 "notes": "免費層唔包當季傷停：唔用上季／平均頂替，Δλ=0"}]
    try:
        data = _fetch(f"injuries?league={DIV_LEAGUE[div]}&season={season}", key)
    except Exception as e:  # noqa: BLE001
        return [{**base, "source": "api-sports", "status": "missing",
                 "notes": f"抓取失敗：{e}"}]
    errs = data.get("errors")
    if errs and (len(errs) if isinstance(errs, list) else len(errs.keys())):
        return [{**base, "source": "api-sports", "status": "missing",
                 "notes": f"上游拒絕：{json.dumps(errs, ensure_ascii=False)[:160]}"}]
    asof = _now()
    rows: list[dict] = []
    for it in data.get("response") or []:
        pl = it.get("player") or {}
        team = it.get("team") or {}
        fx = it.get("fixture") or {}
        rows.append({
            **base,
            "source": "api-sports",
            "source_ts": asof,
            "as_of": fx.get("date") or asof,
            "kickoff_utc": fx.get("date"),
            "player_id": str(pl.get("id")),
            "player_name": pl.get("name"),
            "team": team.get("name"),
            "team_id": str(team.get("id")),
            "injury_type": pl.get("type"),
            "reason": pl.get("reason"),
        })
    if not rows:
        rows = [{**base, "source": "api-sports", "status": "missing",
                 "notes": "上游無紀錄"}]
    time.sleep(1)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--div", required=True, choices=sorted(DIV_LEAGUE))
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--out", default="data/context")
    a = ap.parse_args()
    rows = collect(a.div, a.season, os.environ.get("APISPORTS_API_FOOTBALL_KEY"))
    print(f"appended {cw.append(a.out, 'injuries', rows)} injury row(s)")


if __name__ == "__main__":
    main()
