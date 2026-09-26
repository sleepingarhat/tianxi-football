"""預期入球（xG）採集器 — Understat 五大聯賽，2014/15 至今。［2026-09-26 倉主決定解封，正式列入引擎資料庫］

輸出：
  data/xg/{season}_{LEAGUE}.csv   逐場 xG / npxG / PPDA / deep completions / xPts
  data/xg/_manifest.json          每個聯賽賽季嘅場數、最後成功時間、失敗紀錄

來源狀態：
  understat.com/robots.txt 寫明 Disallow: /。2026-09-26 倉主知悉風險後決定照用（見研究倉 DECISION.md），
  風險由倉主承擔。唔偽裝身份、唔轉 IP、唔繞過任何限制；被拒（4xx/5xx）即停，唔重試轟炸。
  排程：每星期一次，只抓當季（.github/workflows/xg_weekly.yml）。

紀律：
  - 只落賽後統計，供「賽前滾動特徵」用（嚴格向前推一場，特徵引擎負責對齊）。
  - 隊名一律轉成 football-data.co.uk 嘅寫法（`div|date|home|away` 拼 match_key）；
    對唔上就落 unmapped 名單，唔會靜靜哋亂配。
  - 速率上限：每個請求之間至少 1.5 秒。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import os
import time
import urllib.request

BASE = "https://understat.com/getLeagueData"
UA = "tianxi-football-database/1.0 (+https://tianxi.racing)"
OUT_DIR = "data/xg"
SLEEP = 3.0

# Understat 聯賽代號 → football-data.co.uk Div 代號
LEAGUES = {
    "EPL": "E0",
    "La_liga": "SP1",
    "Bundesliga": "D1",
    "Serie_A": "I1",
    "Ligue_1": "F1",
    "RFPL": "RU1",
}

FIELDS = [
    "match_key", "div", "league", "season", "date", "kickoff_utc",
    "home", "away", "hg", "ag",
    "home_xg", "away_xg", "home_npxg", "away_npxg",
    "home_ppda", "away_ppda", "home_deep", "away_deep",
    "home_xpts", "away_xpts", "mapped",
]

# Understat 寫法 → football-data.co.uk 寫法（只列唔一樣嘅）
NAME_MAP = {
    # England
    "Manchester United": "Man United", "Manchester City": "Man City",
    "Newcastle United": "Newcastle", "Wolverhampton Wanderers": "Wolves",
    "Tottenham": "Tottenham", "Sheffield United": "Sheffield United",
    "West Bromwich Albion": "West Brom", "Queens Park Rangers": "QPR",
    "Nottingham Forest": "Nott'm Forest", "Leeds": "Leeds",
    "Huddersfield": "Huddersfield", "Cardiff": "Cardiff",
    # Spain
    "Atletico Madrid": "Ath Madrid", "Athletic Club": "Ath Bilbao",
    "Real Sociedad": "Sociedad", "Real Betis": "Betis",
    "Celta Vigo": "Celta", "Deportivo La Coruna": "La Coruna",
    "Rayo Vallecano": "Vallecano", "Real Valladolid": "Valladolid",
    "Sporting Gijon": "Sp Gijon", "Espanyol": "Espanol",
    "Alaves": "Alaves", "Almeria": "Almeria", "Racing Santander": "Santander",
    "Real Oviedo": "Oviedo", "SD Huesca": "Huesca",
    # Germany
    "Bayern Munich": "Bayern Munich", "Borussia Dortmund": "Dortmund",
    "Borussia M.Gladbach": "M'gladbach", "Bayer Leverkusen": "Leverkusen",
    "Schalke 04": "Schalke 04", "Hertha Berlin": "Hertha", "FC Heidenheim": "Heidenheim",
    "RasenBallsport Leipzig": "RB Leipzig", "FC Cologne": "FC Koln",
    "Eintracht Frankfurt": "Ein Frankfurt", "Hoffenheim": "Hoffenheim",
    "Werder Bremen": "Werder Bremen", "Mainz 05": "Mainz",
    "Fortuna Duesseldorf": "Fortuna Dusseldorf", "Greuther Fuerth": "Greuther Furth",
    "Hamburger SV": "Hamburg", "Nuernberg": "Nurnberg",
    "VfB Stuttgart": "Stuttgart", "Union Berlin": "Union Berlin",
    "Arminia Bielefeld": "Bielefeld", "Paderborn": "Paderborn",
    "Darmstadt": "Darmstadt", "St. Pauli": "St Pauli", "Hannover 96": "Hannover",
    # Italy
    "AC Milan": "Milan", "Internazionale": "Inter", "Hellas Verona": "Verona",
    "Chievo": "Chievo", "AS Roma": "Roma", "Juventus": "Juventus",
    "SPAL 2013": "Spal", "Cagliari": "Cagliari", "Parma Calcio 1913": "Parma",
    # France
    "Paris Saint Germain": "Paris SG", "Saint-Etienne": "St Etienne",
    "Olympique Lyonnais": "Lyon", "Olympique Marseille": "Marseille",
    "Stade Rennais": "Rennes", "Stade Brestois 29": "Brest",
    "Bordeaux": "Bordeaux", "Nimes": "Nimes", "Clermont Foot": "Clermont",
    "Paris FC": "Paris FC", "GFC Ajaccio": "Ajaccio GFCO", "SC Bastia": "Bastia",
}


_KNOWN: dict[str, set[str]] = {}


def known_names(div: str, results_dir: str = "data/results") -> set[str]:
    """由已落地嘅 football-data 賽果 CSV 讀出該聯賽真實隊名，用嚟核對對名結果。"""
    if div in _KNOWN:
        return _KNOWN[div]
    names: set[str] = set()
    if os.path.isdir(results_dir):
        for fn in os.listdir(results_dir):
            if not fn.endswith(f"_{div}.csv"):
                continue
            with open(os.path.join(results_dir, fn), encoding="utf-8", errors="replace") as f:
                for row in csv.DictReader(f):
                    for k in ("HomeTeam", "AwayTeam"):
                        v = (row.get(k) or "").strip()
                        if v:
                            names.add(v)
    _KNOWN[div] = names
    return names


def fd_name(name: str, div: str) -> tuple[str, bool]:
    mapped = NAME_MAP.get(name, name)
    known = known_names(div)
    if not known:
        # 冇賽果快照可核對：只信明文對照表
        return mapped, name in NAME_MAP
    return mapped, mapped in known


def fetch(league: str, season: int) -> dict:
    url = f"{BASE}/{league}/{season}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
                 "Accept-Encoding": "gzip"},
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def ppda(d: dict | None) -> str:
    if not d:
        return ""
    att, def_ = d.get("att"), d.get("def")
    if not att or not def_:
        return ""
    return f"{att / def_:.4f}"


def build_rows(league: str, season: int, data: dict) -> tuple[list[dict], list[str]]:
    div = LEAGUES[league]
    # 逐隊 history 按日期索引，補 npxG / PPDA / deep（dates 只有 xG）
    hist: dict[tuple[str, str], dict] = {}
    for team in data.get("teams", {}).values():
        title = team.get("title", "")
        for h in team.get("history", []):
            hist[(title, h.get("date", "")[:19])] = h

    rows: list[dict] = []
    unmapped: set[str] = set()
    for m in data.get("dates", []):
        if not m.get("isResult"):
            continue
        stamp = (m.get("datetime") or "")[:19]
        home_u = m["h"]["title"]
        away_u = m["a"]["title"]
        home, ok_h = fd_name(home_u, div)
        away, ok_a = fd_name(away_u, div)
        if not ok_h:
            unmapped.add(f"{league}:{home_u}")
        if not ok_a:
            unmapped.add(f"{league}:{away_u}")
        hh = hist.get((home_u, stamp), {})
        ah = hist.get((away_u, stamp), {})
        date = stamp[:10]
        rows.append({
            "match_key": f"{div}|{date}|{home}|{away}",
            "div": div,
            "league": league,
            "season": season,
            "date": date,
            "kickoff_utc": stamp,
            "home": home,
            "away": away,
            "hg": m.get("goals", {}).get("h", ""),
            "ag": m.get("goals", {}).get("a", ""),
            "home_xg": m.get("xG", {}).get("h", ""),
            "away_xg": m.get("xG", {}).get("a", ""),
            "home_npxg": hh.get("npxG", ""),
            "away_npxg": ah.get("npxG", ""),
            "home_ppda": ppda(hh.get("ppda")),
            "away_ppda": ppda(ah.get("ppda")),
            "home_deep": hh.get("deep", ""),
            "away_deep": ah.get("deep", ""),
            "home_xpts": hh.get("xpts", ""),
            "away_xpts": ah.get("xpts", ""),
            "mapped": int(ok_h and ok_a),
        })
    rows.sort(key=lambda r: (r["kickoff_utc"], r["home"]))
    return rows, sorted(unmapped)


def write_csv(path: str, rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    with open(path, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--seasons", default="2014:2026", help="起:止（開季年，含止）")
    ap.add_argument("--leagues", default="EPL,La_liga,Bundesliga,Serie_A,Ligue_1",
                    help="默認五大聯賽；football-data.co.uk 冇俄超賽果，RFPL 對唔到名要自己加")
    args = ap.parse_args()

    lo, hi = (int(x) for x in args.seasons.split(":"))
    leagues = [x for x in args.leagues.split(",") if x in LEAGUES]
    os.makedirs(args.out, exist_ok=True)

    mpath = os.path.join(args.out, "_manifest.json")
    man = {"files": {}, "unmapped": [], "fail": []}
    if os.path.exists(mpath):
        try:
            man = json.load(open(mpath, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    man.setdefault("files", {})
    unmapped: set[str] = set(man.get("unmapped", []))
    fails: list[str] = []
    total = 0

    for league in leagues:
        for season in range(lo, hi + 1):
            key = f"{season}_{league}"
            try:
                data = fetch(league, season)
                rows, um = build_rows(league, season, data)
            except Exception as exc:  # noqa: BLE001
                fails.append(f"{key}: {type(exc).__name__} {exc}")
                print(f"[fail] {key}: {exc}")
                time.sleep(SLEEP)
                continue
            if not rows:
                print(f"[skip] {key}: 冇已完成場次")
                time.sleep(SLEEP)
                continue
            write_csv(os.path.join(args.out, f"{key}.csv"), rows)
            unmapped.update(um)
            bad = sum(1 for r in rows if not r["mapped"])
            man["files"][key] = {
                "rows": len(rows),
                "unmapped_rows": bad,
                "first": rows[0]["date"],
                "last": rows[-1]["date"],
                "fetched_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            }
            total += len(rows)
            print(f"[ok] {key}: {len(rows)} 場（未對名 {bad}）")
            time.sleep(SLEEP)

    man["unmapped"] = sorted(unmapped)
    man["fail"] = fails
    man["last_run_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    man["last_run_ok"] = not fails
    man["rows_this_run"] = total
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")

    print(f"總共 {total} 場；失敗 {len(fails)}；未對名隊名 {len(unmapped)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
