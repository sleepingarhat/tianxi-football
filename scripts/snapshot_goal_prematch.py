#!/usr/bin/env python3
"""賽前傷停／陣容快照（S9 缺陣實力特徵嘅原材料，只記事實）。

來源：GOAL API /fixtures/date/{d}（分頁）＋ /fixtures/{id}/lineups（startingLineups、substitutes、missingPlayers）。
- 只影五大聯賽、未開波嘅場次；每次都 append 一行，記 observed_ts（我哋收到嘅時間），唔覆寫舊快照。
- 回測只准用 observed_ts < 鎖定線（開賽前 60 分鐘）嘅最後一份，避免偷睇。
- 唔生成特徵、唔改凍結預測、唔碰指紋。
輸出：data/prematch_goal/YYYY-MM-DD.jsonl（按開賽日）
"""
import json, os, re, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://api.goal-api.com/v1"
TOP5 = {("England", r"^premier league$"): "E0", ("Spain", r"^(laliga|la liga|primera division)"): "SP1",
        ("Italy", r"^serie a$"): "I1", ("Germany", r"^bundesliga$"): "D1", ("France", r"^ligue 1$"): "F1"}

def get(path, key, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {key}", "User-Agent": "tianxi-snapshot/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(5 * (i + 1))

def div_of(r):
    for (c, pat), d in TOP5.items():
        if r.get("countryName") == c and re.search(pat, str(r.get("leagueName", "")).strip().lower()):
            return d
    return None

def slim(p):
    return {"name": p.get("lineupPlayer") or p.get("playerName"), "num": p.get("lineupNumber"),
            "pos": p.get("lineupPosition") or p.get("playerPosition"), "reason": p.get("reason") or p.get("type")}

def main():
    key = os.environ.get("GOAL_API_KEY")
    if not key: print("GOAL_API_KEY missing"); sys.exit(1)
    now = datetime.now(timezone.utc); obs = now.isoformat(timespec="seconds")
    ok = fail = 0
    for day in (now.date(), now.date() + timedelta(days=1)):
        off, fx = 0, []
        while True:
            j = get(f"/fixtures/date/{day}?limit=100&offset={off}", key)
            fx += j.get("data", []); pg = j.get("pagination", {})
            if not pg.get("hasMore"): break
            off += 100; time.sleep(0.3)
        os.makedirs("data/prematch_goal", exist_ok=True)
        out = open(f"data/prematch_goal/{day}.jsonl", "a")
        for r in fx:
            d = div_of(r)
            if not d or r.get("matchPeriod") not in (None, "NOT_STARTED"): continue
            try:
                lu = get(f"/fixtures/{r['id']}/lineups", key).get("data", {})
                side = lambda s: {"xi": [slim(p) for p in lu.get(s, {}).get("startingLineups", [])],
                                  "subs": [slim(p) for p in lu.get(s, {}).get("substitutes", [])],
                                  "missing": [slim(p) for p in lu.get(s, {}).get("missingPlayers", [])]}
                h, a = side("home"), side("away")
                out.write(json.dumps({"observed_ts": obs, "div": d, "goal_id": r["id"], "kickoff_utc": r.get("kickoffUtc"),
                    "home": r.get("homeTeamName"), "away": r.get("awayTeamName"), "home_lu": h, "away_lu": a,
                    "lineup_published": bool(h["xi"] and a["xi"])}, ensure_ascii=False) + "\n"); ok += 1
            except Exception as e:
                fail += 1; print("fail", r.get("id"), e)
            time.sleep(0.3)
        out.close()
    os.makedirs("data/_status", exist_ok=True)
    json.dump({"job": "prematch_goal", "run_at": obs, "snapshots": ok, "failed": fail},
              open("data/_status/prematch_goal.json", "w"), ensure_ascii=False, indent=1)
    print("snapshots", ok, "failed", fail)
    if ok == 0 and fail > 0: sys.exit(1)

main()
