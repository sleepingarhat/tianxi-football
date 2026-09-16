#!/usr/bin/env python3
"""公布名單時間戳採集（S29 資料層，只記事實）。

- 對每場賽前場次，記 lineup_published_ts（上游有先寫）同 lineup_observed_ts（我哋首次見到）。
- 冇名單／遲過鎖定線／唔齊 11 人 → status 標明、eligible=false → Δλ=0。
- 唔生成 δ、唔改凍結預測、唔碰指紋、唔寫 data/predictions。
- 冇授權 API key → 只寫 status=missing 佔位行並告警，唔用任何替代值。
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone

import context_write as cw

API_HOST = "https://v3.football.api-sports.io"
AF_HOST = "https://apiv3.apifootball.com/"
DIV_LEAGUE = {"E0": 39, "D1": 78, "SP1": 140, "I1": 135, "F1": 61}
AF_LEAGUE = {"E0": 152, "D1": 175, "SP1": 302, "I1": 207, "F1": 168}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fetch(path: str, key: str) -> dict:
    req = urllib.request.Request(f"{API_HOST}/{path}", headers={"x-apisports-key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def collect(fixtures: list[dict], key: str | None) -> list[dict]:
    rows: list[dict] = []
    for fx in fixtures:
        base = {
            "match_key": fx.get("match_key"),
            "div": fx.get("div"),
            "kickoff_utc": fx.get("kickoff_utc"),
            "team_h": fx.get("home"),
            "team_a": fx.get("away"),
        }
        if not key:
            rows.append({**base, "source": "none", "source_kind": "aggregator",
                         "status": "missing",
                         "notes": "冇授權名單來源 key：唔用替代值，Δλ=0"})
            continue
        try:
            data = _fetch(f"fixtures/lineups?fixture={fx['provider_id']}", key)
            arr = data.get("response") or []
        except Exception as e:  # noqa: BLE001
            rows.append({**base, "source": "api-football", "source_kind": "aggregator",
                         "status": "missing", "notes": f"抓取失敗：{e}"})
            continue
        if len(arr) < 2:
            rows.append({**base, "source": "api-football", "source_kind": "aggregator",
                         "status": "missing", "notes": "上游未公布名單"})
            continue
        n_h = len((arr[0].get("startXI") or []))
        n_a = len((arr[1].get("startXI") or []))
        rows.append({
            **base,
            "source": "api-football",
            "source_kind": "aggregator",
            "source_ts": _now(),
            "lineup_published_ts": None,   # 上游唔提供公布時間戳
            "lineup_observed_ts": _now(),  # 保守上界：我哋首次見到
            "starters_h": n_h,
            "starters_a": n_a,
            "complete_h": n_h == 11,
            "complete_a": n_a == 11,
        })
    return rows


def _af(qs: str, key: str) -> object:
    url = f"{AF_HOST}?{qs}&APIkey={key}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def collect_current(fixtures: list[dict], key: str | None) -> list[dict]:
    """當季名單：API-SPORTS 免費層唔包當季，改用 apifootball.com get_events 嘅 lineup。

    上游一律唔提供「公布時間戳」，所以只記 lineup_observed_ts＝我哋首次見到
    先發齊 11 人嘅時間（保守上界）。要真正合格入 δ，必須連續輪詢：
    未公布就寫 missing 佔位，公布咗嗰刻嘅 observed_ts 才係唯一合法開關。
    """
    rows: list[dict] = []
    for fx in fixtures:
        base = {
            "match_key": fx.get("match_key"),
            "div": fx.get("div"),
            "kickoff_utc": fx.get("kickoff_utc"),
            "team_h": fx.get("home"),
            "team_a": fx.get("away"),
            "source": "apifootball" if key else "none",
            "source_kind": "aggregator",
        }
        if not key or not fx.get("af_match_id"):
            rows.append({**base, "status": "missing",
                         "notes": "冇授權當季名單來源／冇對上 match id：唔用替代值，Δλ=0"})
            continue
        try:
            data = _af(f"action=get_events&match_id={fx['af_match_id']}", key)
            ev = data[0] if isinstance(data, list) and data else {}
            lu = ev.get("lineup") or {}
            n_h = len(((lu.get("home") or {}).get("starting_lineups")) or [])
            n_a = len(((lu.get("away") or {}).get("starting_lineups")) or [])
        except Exception as e:  # noqa: BLE001
            rows.append({**base, "status": "missing", "notes": f"抓取失敗：{e}"})
            continue
        if n_h == 0 and n_a == 0:
            rows.append({**base, "source_ts": _now(), "status": "missing",
                         "starters_h": 0, "starters_a": 0,
                         "notes": "上游未公布名單（繼續輪詢）"})
            continue
        rows.append({
            **base,
            "source_ts": _now(),
            "lineup_published_ts": None,   # 上游唔提供公布時間戳
            "lineup_observed_ts": _now(),  # 保守上界：我哋首次見到
            "starters_h": n_h,
            "starters_a": n_a,
            "complete_h": n_h == 11,
            "complete_a": n_a == 11,
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True, help="賽前場次 JSON（list）")
    ap.add_argument("--out", default="data/context")
    ap.add_argument("--source", choices=("current", "historic"), default="current")
    a = ap.parse_args()
    with open(a.fixtures, encoding="utf-8") as f:
        fixtures = json.load(f)
    if a.source == "current":
        rows = collect_current(fixtures, os.environ.get("API_FOOTBALL_KEY"))
    else:
        rows = collect(fixtures, os.environ.get("APISPORTS_API_FOOTBALL_KEY"))
    n = cw.append(a.out, "lineups", rows)
    print(f"appended {n} lineup row(s)")


if __name__ == "__main__":
    main()
