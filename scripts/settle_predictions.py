#!/usr/bin/env python3
"""S13 結算器 — 賽果 join 凍結帳，只寫賽果欄，永不重算模型

流程：
  1. 讀 data/results/*.csv（football-data.co.uk 口徑，90 分鐘全場賽果）。
  2. 逐條凍結帳（data/predictions/log/YYYY-MM.json）未結算嘅場次，按
     div|DD/MM/YYYY|home|away join。
  3. 只寫 result 欄：ft_h／ft_a／ftr／rps／argmax_hit／p_actual／波膽格對帳。
     預測欄（p／lambda／cs／fingerprint）一分不改。
  4. 對唔上名嘅入 unmapped 報告，唔智能亂配。
  5. 出公開讀口 data/predictions/hit_rate.json（五大聯賽、綠燈場先入帳）。

鐵律：完場對帳只准 join 凍結列，禁止用最新模型重打已完場。
"""
import csv, glob, json, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "data", "results")
LOG_DIR = os.path.join(ROOT, "data", "predictions", "log")
HIT = os.path.join(ROOT, "data", "predictions", "hit_rate.json")
SNAP_DIR = os.path.join(ROOT, "snapshots")

BIG5 = ("E0", "D1", "SP1", "I1", "F1")
GREEN = ("final",)  # 綠燈＝已鎖且模型身分為對外公開軌；現時凍結軌仍為 fallback（紅燈）
IDX = {"home": 0, "draw": 1, "away": 2}


def rps3(p, outcome):
    o = [0.0, 0.0, 0.0]
    o[IDX[outcome]] = 1.0
    cp = cq = 0.0
    tot = 0.0
    for k in range(2):
        cp += p[k]
        cq += o[k]
        tot += (cp - cq) ** 2
    return tot / 2.0


def load_results():
    book = {}
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "*.csv"))):
        if os.path.basename(path).startswith("baseline"):
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d = (r.get("Date") or "").strip()
                if len(d) < 8 or not r.get("HomeTeam") or not r.get("AwayTeam"):
                    continue
                dd, mm, yy = d.split("/")
                yy = yy if len(yy) == 4 else ("20" + yy if int(yy) < 50 else "19" + yy)
                try:
                    gh, ga = int(float(r["FTHG"])), int(float(r["FTAG"]))
                except (ValueError, TypeError, KeyError):
                    continue
                key = f"{r.get('Div','')}|{dd.zfill(2)}/{mm.zfill(2)}/{yy}|{r['HomeTeam'].strip()}|{r['AwayTeam'].strip()}"
                book[key] = (gh, ga)
    return book


def settle_one(rec, gh, ga):
    ftr = "home" if gh > ga else ("draw" if gh == ga else "away")
    p = rec.get("p") or []
    res = {"ft_h": gh, "ft_a": ga, "ftr": ftr,
           "settled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if len(p) == 3:
        res["rps"] = round(rps3(p, ftr), 5)
        res["p_actual"] = round(p[IDX[ftr]], 4)
        res["argmax_hit"] = int(max(range(3), key=lambda i: p[i]) == IDX[ftr])
    cs = rec.get("cs") or {}
    top8 = cs.get("top8") or []
    label = f"{gh}-{ga}"
    rank = next((i + 1 for i, c in enumerate(top8) if c.get("score") == label), None)
    res["cs_rank"] = rank
    res["cs_top1"] = int(rank == 1) if rank else 0
    res["cs_top3"] = int(rank is not None and rank <= 3)
    res["cs_top8"] = int(rank is not None)
    res["cs_p_actual"] = next((c.get("p") for c in top8 if c.get("score") == label), None)
    tails = cs.get("tails") or {}
    if tails:
        res["tails_actual"] = {"win_by_3plus": int(gh - ga >= 3), "home_4plus": int(gh >= 4),
                               "away_clean_sheet": int(ga == 0)}
    return res


def bucket_label(x):
    lo = int(x * 10) * 10
    return f"{lo}-{lo + 10}%"


def aggregate(records):
    """records：已結算列。回傳平均 RPS、校準表、波膽覆蓋。"""
    if not records:
        return None
    n = len(records)
    rps = sum(r["result"]["rps"] for r in records if "rps" in r["result"])
    hits = sum(r["result"].get("argmax_hit", 0) for r in records)
    buckets = defaultdict(lambda: {"n": 0, "hit": 0, "p_sum": 0.0})
    for r in records:
        p = r["p"]
        ftr = r["result"]["ftr"]
        for i, key in enumerate(("home", "draw", "away")):
            b = buckets[bucket_label(p[i])]
            b["n"] += 1
            b["p_sum"] += p[i]
            b["hit"] += int(ftr == key)
    calib = [{"band": k, "n": v["n"], "p_avg": round(v["p_sum"] / v["n"], 4),
              "actual": round(v["hit"] / v["n"], 4)}
             for k, v in sorted(buckets.items(), key=lambda kv: kv[0])]
    ece = sum(v["n"] * abs(v["p_sum"] / v["n"] - v["hit"] / v["n"]) for v in buckets.values())
    ece /= max(sum(v["n"] for v in buckets.values()), 1)
    return {
        "n": n,
        "rps_avg": round(rps / n, 4),
        "argmax_hit_rate": round(hits / n, 4),
        "ece": round(ece, 4),
        "cs_top1": round(sum(r["result"].get("cs_top1", 0) for r in records) / n, 4),
        "cs_top3": round(sum(r["result"].get("cs_top3", 0) for r in records) / n, 4),
        "cs_top8": round(sum(r["result"].get("cs_top8", 0) for r in records) / n, 4),
        "fingerprints": sorted({r.get("fingerprint") for r in records if r.get("fingerprint")}),
    }


def main():
    results = load_results()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    grace = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
    settled = 0
    unmatched = []
    all_recs = []
    for path in sorted(glob.glob(os.path.join(LOG_DIR, "*.json"))):
        book = json.load(open(path, encoding="utf-8"))
        dirty = False
        for key, rec in book.get("matches", {}).items():
            if rec.get("result"):
                all_recs.append(rec)
                continue
            hit = results.get(key)
            if hit is None:
                ko = rec.get("kickoff_utc") or ""
                # 賽果採集有延遲，開賽後 3 日內未 join 唔當對唔上名
                if ko and ko < grace:
                    unmatched.append(key)
                continue
            rec["result"] = settle_one(rec, hit[0], hit[1])
            settled += 1
            dirty = True
            all_recs.append(rec)
        if dirty:
            book["meta"]["settled_at"] = now
            json.dump(book, open(path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1, sort_keys=True)

    done = [r for r in all_recs if r.get("result") and r.get("p")]
    big5 = [r for r in done if r.get("div") in BIG5]
    green = [r for r in big5 if r.get("status") in GREEN and r.get("locked_at")]
    report = {
        "generated_at": now,
        "scope": {"big5": list(BIG5), "green_status": list(GREEN),
                  "note": "頁頂戰績只收五大聯賽、綠燈（對外公開軌）且已鎖場次；紅燈退回基準軌只作診斷"},
        "baselines": {"prior_asof": 0.2261, "market_devig": 0.2047, "s5_backtest": 0.2098},
        "green": aggregate(green),
        "diagnostic_big5_all_lights": aggregate(big5),
        "diagnostic_all_leagues": aggregate(done),
        "settled_this_run": settled,
        "unmatched_count": len(unmatched),
        "unmatched_sample": unmatched[:20],
    }
    os.makedirs(os.path.dirname(HIT), exist_ok=True)
    json.dump(report, open(HIT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.makedirs(SNAP_DIR, exist_ok=True)
    day = now[:10]
    json.dump(report, open(os.path.join(SNAP_DIR, f"settle_{day}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in report.items() if k != "unmatched_sample"},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
