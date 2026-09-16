#!/usr/bin/env python3
"""球員檔案採集（S35 資料層，只記事實）。

來源：API-SPORTS /players/squads（免費層當季名單可讀，唔使 season 參數）。
- 只記事實：球員 id／名／號碼／位置／國籍／出生日／身高體重／官方相片連結。
- photo_url 只存連結，唔下載、唔重新託管；photo_license 記明來源條款狀態，
  公開展示之前要逐源確認授權，未確認一律唔上前台。
- 唔生成 δ、唔估 λ、唔碰 data/predictions／models／指紋。
- 冇 key／抓唔到 → 寫 status=missing 佔位行，唔用任何替代值。
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
PHOTO_LICENSE = "api-sports:media-link-unverified"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fetch(path: str, key: str) -> dict:
    req = urllib.request.Request(f"{API_HOST}/{path}", headers={"x-apisports-key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _int(v) -> int | None:
    try:
        return int(str(v).split()[0])
    except (TypeError, ValueError):
        return None


def collect(teams: list[dict], key: str | None) -> list[dict]:
    """teams: [{team_id, team, div}]"""
    rows: list[dict] = []
    for t in teams:
        base = {"div": t.get("div"), "team": t.get("team"), "team_id": str(t.get("team_id"))}
        if not key:
            rows.append({**base, "source": "none", "status": "missing",
                         "notes": "冇授權球員來源 key：唔用替代值"})
            continue
        try:
            data = _fetch(f"players/squads?team={t['team_id']}", key)
            resp = (data.get("response") or [])
            squad = (resp[0].get("players") or []) if resp else []
        except Exception as e:  # noqa: BLE001
            rows.append({**base, "source": "api-sports", "status": "missing",
                         "notes": f"抓取失敗：{e}"})
            time.sleep(1)
            continue
        if not squad:
            rows.append({**base, "source": "api-sports", "status": "missing",
                         "notes": "上游未提供名單"})
            time.sleep(1)
            continue
        asof = _now()
        for p in squad:
            rows.append({
                **base,
                "source": "api-sports",
                "source_ts": asof,
                "as_of": asof,
                "player_id": str(p.get("id")),
                "player_name": p.get("name"),
                "shirt_number": _int(p.get("number")),
                "position": p.get("position"),
                "age": _int(p.get("age")),
                "photo_url": p.get("photo") or None,
                "photo_license": PHOTO_LICENSE,
            })
        time.sleep(1)  # 免費層限速：每秒一個請求
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teams", required=True, help="球隊 JSON（list：team_id／team／div）")
    ap.add_argument("--out", default="data/context")
    a = ap.parse_args()
    with open(a.teams, encoding="utf-8") as f:
        teams = json.load(f)
    rows = collect(teams, os.environ.get("APISPORTS_API_FOOTBALL_KEY"))
    print(f"appended {cw.append(a.out, 'players', rows)} player row(s)")


if __name__ == "__main__":
    main()
