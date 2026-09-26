"""合併 xG：data/xg/ 係引擎讀嘅正式 xG 檔。

- 2021/22 季起：以 PitchAPI（data/xg_pitchapi/）為正選，逐場用 match_key 對；PitchAPI 冇嘅場先用 Understat 補。
- 2021/22 季之前：PitchAPI 冇歷史，照用 Understat（data/xg_understat/）。
- 每行加 source 欄（pitchapi／understat）。ppda、deep、xpts 只有 Understat 有，PitchAPI 場保留 Understat 值（冇就留空）。
- 只寫資料檔，唔郁凍結預測、指紋、模型。
"""
import csv, glob, os

UND, PIT, OUT = "data/xg_understat", "data/xg_pitchapi", "data/xg"
LEAGUE = {"E0": "EPL", "SP1": "La_liga", "I1": "Serie_A", "D1": "Bundesliga", "F1": "Ligue_1"}
FIRST_PITCH_SEASON = 2021
COLS = ["match_key", "div", "league", "season", "date", "kickoff_utc", "home", "away", "hg", "ag",
        "home_xg", "away_xg", "home_npxg", "away_npxg", "home_ppda", "away_ppda", "home_deep", "away_deep",
        "home_xpts", "away_xpts", "mapped", "home_xgot", "away_xgot", "source"]

os.makedirs(OUT, exist_ok=True)
seasons = sorted({int(os.path.basename(f)[:4]) for f in glob.glob(f"{UND}/*.csv") + glob.glob(f"{PIT}/*_*.csv")})
stats = {}
for s in seasons:
    for div, lg in LEAGUE.items():
        uf, pf = f"{UND}/{s}_{lg}.csv", f"{PIT}/{s}_{div}.csv"
        und = {r["match_key"]: r for r in csv.DictReader(open(uf))} if os.path.exists(uf) else {}
        pit = {r["match_key"]: r for r in csv.DictReader(open(pf))} if (s >= FIRST_PITCH_SEASON and os.path.exists(pf)) else {}
        if not und and not pit: continue
        rows = {}
        for k, r in und.items():
            rows[k] = {**{c: r.get(c, "") for c in COLS}, "source": "understat"}
        for k, p in pit.items():
            if p.get("mapped") != "1": continue
            base = rows.get(k, {c: "" for c in COLS})
            base.update({c: p[c] for c in ("match_key", "div", "season", "date", "kickoff_utc", "home", "away", "hg", "ag",
                                            "home_xg", "away_xg", "home_npxg", "away_npxg", "home_xgot", "away_xgot")})
            base.update(league=lg, mapped="1", source="pitchapi")
            rows[k] = base
        with open(f"{OUT}/{s}_{lg}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, COLS); w.writeheader()
            for r in sorted(rows.values(), key=lambda r: (r["date"], r["home"])): w.writerow(r)
        n_p = sum(1 for r in rows.values() if r["source"] == "pitchapi")
        stats[f"{s}_{lg}"] = (len(rows), n_p)
for k, v in stats.items(): print(k, "總", v[0], "PitchAPI", v[1])
