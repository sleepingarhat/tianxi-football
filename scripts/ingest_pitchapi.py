"""PitchAPI 五大聯賽逐場 xG 收料（S9 正選 xG 源，2026-09-26 起）。

- 逐日讀 /date/{d}，只取五大聯賽完場賽事，再讀 /matches/{id}/shots，逐腳加總。
- 隊名對返 football-data.co.uk 名（data/results/{季}_{div}.csv，同日 ±1、同比分、名相似），
  所以 match_key 同 Understat 檔完全同一格式：div|date|home|away。
- 輸出 data/xg_pitchapi/{季}_{div}.csv，按 match_key 合併，重跑唔會重複。
- 只寫資料檔，唔郁凍結預測、指紋、模型；賠率權重仍然 0。
用法：python scripts/ingest_pitchapi.py --from 2026-09-01 --to 2026-09-26
"""
import argparse, csv, datetime as dt, difflib, glob, json, os, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

BASE = "https://api.pitchapi.dev/v1"
LEAGUES = {("ENG", "Premier League"): "E0", ("ESP", "LaLiga"): "SP1", ("ITA", "Serie A"): "I1",
           ("GER", "Bundesliga"): "D1", ("FRA", "Ligue 1"): "F1"}
COLS = ["match_key", "div", "season", "date", "kickoff_utc", "home", "away", "hg", "ag",
        "home_xg", "away_xg", "home_npxg", "away_npxg", "home_xgot", "away_xgot",
        "home_shots", "away_shots", "pitch_id", "pitch_home", "pitch_away", "mapped"]
KEY = os.environ["PITCHAPI_KEY"]


def get(path):
    for i in range(6):
        try:
            req = urllib.request.Request(BASE + path, headers={"X-API-KEY": KEY, "User-Agent": "tianxi-ingest"})
            with urllib.request.urlopen(req, timeout=40) as r:
                j = json.load(r)
            return j.get("data", j) if isinstance(j, dict) else j
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504): time.sleep(3 * (i + 1)); continue
            if e.code == 404: return None
            raise
        except Exception:
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"PitchAPI 連續失敗：{path}")


def season_of(d): return d.year if d.month >= 7 else d.year - 1


_res = {}
def results(season, div, root):
    k = (season, div)
    if k not in _res:
        rows = []
        f = f"{root}/{season}_{div}.csv"
        if os.path.exists(f):
            for r in csv.DictReader(open(f, encoding="latin-1")):
                try:
                    dd = dt.datetime.strptime(r["Date"], "%d/%m/%Y" if len(r["Date"]) == 10 else "%d/%m/%y").date()
                    rows.append((dd, r["HomeTeam"], r["AwayTeam"], int(r["FTHG"]), int(r["FTAG"])))
                except Exception:
                    pass
        _res[k] = rows
    return _res[k]


def sim(a, b): return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def map_names(div, d, home, away, hg, ag, root, tmap):
    if home in tmap and away in tmap: return tmap[home], tmap[away], 1
    cand = [r for r in results(season_of(d), div, root) if abs((r[0] - d).days) <= 1 and r[3] == hg and r[4] == ag]
    if cand:
        best = max(cand, key=lambda r: sim(home, r[1]) + sim(away, r[2]))
        ok = (tmap.get(home) == best[1]) or (tmap.get(away) == best[2]) or sim(home, best[1]) + sim(away, best[2]) >= 0.9 or len(cand) == 1
        if ok:
            tmap[home], tmap[away] = best[1], best[2]
            return best[1], best[2], 1
    return home, away, 0


def one_match(m, div, root, tmap):
    sh = get(f"/matches/{m['id']}/shots") or {}
    shots = [s for p in (sh.get("periods") or []) for s in (p.get("shots") or [])]
    hid, aid = m["home_team"]["id"], m["away_team"]["id"]
    agg = {t: dict(xg=0.0, np=0.0, ot=0.0, n=0) for t in (hid, aid)}
    for s in shots:
        a = agg.get(s.get("team_id"))
        if a is None: continue
        x = float(s.get("expected_goals") or 0)
        a["xg"] += x; a["n"] += 1; a["ot"] += float(s.get("expected_goals_on_target") or 0)
        if s.get("situation") != "Penalty": a["np"] += x
    return m, shots, agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d0", required=True); ap.add_argument("--to", dest="d1", required=True)
    ap.add_argument("--out", default="data/xg_pitchapi"); ap.add_argument("--results", default="data/results")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    mp = f"{a.out}/team_map.json"
    tmap = json.load(open(mp)) if os.path.exists(mp) else {}
    leagues = {l["id"]: LEAGUES[(l["country_code"], l["name"])] for l in get("/leagues")["leagues"]
               if (l.get("country_code"), l.get("name")) in LEAGUES}
    d, end = dt.date.fromisoformat(a.d0), dt.date.fromisoformat(a.d1)
    buckets, n_new, n_unmapped = {}, 0, 0
    while d <= end:
        arr = get(f"/date/{d}") or []
        arr = arr if isinstance(arr, list) else arr.get("matches", [])
        todo = [(m, leagues[m["league"]["id"]]) for m in arr
                if (m.get("league") or {}).get("id") in leagues and m.get("status") == "finished"
                and m.get("score_home") is not None]
        with ThreadPoolExecutor(a.workers) as ex:
            got = list(ex.map(lambda t: one_match(t[0], t[1], a.results, tmap), todo))
        for (m, shots, agg), (_, div) in zip(got, todo):
            if not shots: continue  # 未有射門數據就唔寫，下次再補
            hg, ag = int(m["score_home"]), int(m["score_away"])
            h, w, ok = map_names(div, d, m["home_team"]["name"], m["away_team"]["name"], hg, ag, a.results, tmap)
            n_unmapped += 0 if ok else 1
            H, A = agg[m["home_team"]["id"]], agg[m["away_team"]["id"]]
            s = season_of(d)
            row = dict(match_key=f"{div}|{d}|{h}|{w}", div=div, season=s, date=str(d), kickoff_utc=m.get("time_utc", ""),
                       home=h, away=w, hg=hg, ag=ag, home_xg=round(H["xg"], 5), away_xg=round(A["xg"], 5),
                       home_npxg=round(H["np"], 5), away_npxg=round(A["np"], 5), home_xgot=round(H["ot"], 5),
                       away_xgot=round(A["ot"], 5), home_shots=H["n"], away_shots=A["n"], pitch_id=m["id"],
                       pitch_home=m["home_team"]["name"], pitch_away=m["away_team"]["name"], mapped=int(ok))
            buckets.setdefault((s, div), {})[row["match_key"]] = row; n_new += 1
        print(d, len(todo), flush=True)
        d += dt.timedelta(days=1)
    for (s, div), rows in buckets.items():
        f = f"{a.out}/{s}_{div}.csv"
        old = {r["match_key"]: r for r in csv.DictReader(open(f))} if os.path.exists(f) else {}
        old.update(rows)
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, COLS); w.writeheader()
            for r in sorted(old.values(), key=lambda r: (r["date"], r["home"])): w.writerow(r)
    json.dump(dict(sorted(tmap.items())), open(mp, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(dict(matches=n_new, unmapped=n_unmapped)), flush=True)


if __name__ == "__main__":
    sys.exit(main())
