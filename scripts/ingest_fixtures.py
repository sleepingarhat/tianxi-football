"""賽前賽程採集器 — football-data.co.uk fixtures.csv（未來約一週賽程 + 賽前賠率）。

輸出：
  data/fixtures/upcoming.csv   標準化賽程（開賽時間為英國時區，另附 UTC）
  data/fixtures/upcoming.json  給網站／引擎讀（含 freshness meta）

紀律：
  - 賠率欄照落，但標明 odds_track = true，市場欄永不進入排名（market_beta = 0）。
  - 抓唔到就保留上一份，並喺 meta 標 stale = true 同上次成功時間，唔會靜靜哋出舊貨。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import time
import urllib.request

URL = "https://www.football-data.co.uk/fixtures.csv"
UA = "tianxi-football-database/1.0 (+https://tianxi.racing)"
OUT_DIR = "data/fixtures"

try:
    from zoneinfo import ZoneInfo

    UK = ZoneInfo("Europe/London")
except Exception:  # noqa: BLE001
    UK = dt.UTC

FIELDS = [
    "match_key", "div", "league_zh", "date_uk", "time_uk", "kickoff_utc",
    "home", "away", "referee",
    "odds_h", "odds_d", "odds_a", "odds_o25", "odds_u25", "ah_line", "ah_h", "ah_a",
]

DIVS_ZH = {
    "E0": "英超", "E1": "英冠", "E2": "英甲", "E3": "英乙", "EC": "英足協全國聯賽",
    "SC0": "蘇超", "SC1": "蘇冠", "SC2": "蘇甲", "SC3": "蘇乙",
    "D1": "德甲", "D2": "德乙", "I1": "意甲", "I2": "意乙",
    "SP1": "西甲", "SP2": "西乙", "F1": "法甲", "F2": "法乙",
    "N1": "荷甲", "B1": "比甲", "P1": "葡超", "T1": "土超", "G1": "希超",
}


def fetch(tries: int = 3) -> bytes | None:
    for i in range(tries):
        try:
            req = urllib.request.Request(URL, headers={"user-agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                print(f"! fetch failed: {e}")
                return None
            time.sleep(2 * (i + 1))
    return None


def to_utc(date_uk: str, time_uk: str) -> str:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            d = dt.datetime.strptime(date_uk, fmt).date()
            break
        except ValueError:
            d = None
    if d is None:
        return ""
    try:
        hh, mm = (int(x) for x in time_uk.split(":")[:2])
    except Exception:  # noqa: BLE001
        hh, mm = 12, 0
    local = dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=UK)
    return local.astimezone(dt.UTC).isoformat(timespec="minutes")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    csv_path = os.path.join(OUT_DIR, "upcoming.csv")
    json_path = os.path.join(OUT_DIR, "upcoming.json")
    prev = {}
    if os.path.exists(json_path):
        prev = json.load(open(json_path, encoding="utf-8"))

    raw = fetch()
    now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    if raw is None:
        prev.setdefault("meta", {}).update(stale=True, last_attempt=now)
        json.dump(prev, open(json_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print("kept previous fixtures (marked stale)")
        return 0  # 契約：壞咗都照有貨，唔當硬失敗

    rdr = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
    rows: list[dict[str, str]] = []
    for r in rdr:
        home, away = (r.get("HomeTeam") or "").strip(), (r.get("AwayTeam") or "").strip()
        if not home or not away:
            continue
        div = (r.get("Div") or "").strip()
        date_uk = (r.get("Date") or "").strip()
        time_uk = (r.get("Time") or "").strip()
        g = lambda k: (r.get(k) or "").strip()  # noqa: E731
        rows.append({
            "match_key": f"{div}|{date_uk}|{home}|{away}",
            "div": div, "league_zh": DIVS_ZH.get(div, ""),
            "date_uk": date_uk, "time_uk": time_uk, "kickoff_utc": to_utc(date_uk, time_uk),
            "home": home, "away": away, "referee": g("Referee"),
            "odds_h": g("AvgH") or g("B365H"), "odds_d": g("AvgD") or g("B365D"),
            "odds_a": g("AvgA") or g("B365A"),
            "odds_o25": g("Avg>2.5") or g("B365>2.5"), "odds_u25": g("Avg<2.5") or g("B365<2.5"),
            "ah_line": g("AHh"), "ah_h": g("AvgAHH"), "ah_a": g("AvgAHA"),
        })
    rows.sort(key=lambda x: (x["kickoff_utc"] or "9999", x["div"], x["home"]))

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    leagues = sorted({r["div"] for r in rows})
    payload = {
        "meta": {
            "source": "football-data.co.uk/fixtures.csv",
            "market_beta": 0,
            "odds_track": True,
            "note": "賠率只作對照軌與價值注判斷，永不進入排名",
            "stale": False,
            "last_success": now,
            "last_attempt": now,
            "count": len(rows),
            "leagues": leagues,
            "first_kickoff_utc": rows[0]["kickoff_utc"] if rows else "",
            "last_kickoff_utc": rows[-1]["kickoff_utc"] if rows else "",
        },
        "fixtures": rows,
    }
    json.dump(payload, open(json_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"fixtures {len(rows)} across {len(leagues)} leagues")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
