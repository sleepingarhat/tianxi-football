#!/usr/bin/env python3
"""ClubElo × 自建 Elo 對帳（silver as-of）

角色：ClubElo 係外部尺，只做對帳同覆蓋率報告。
  · 自建 elo_s2 仍然係唯一入模型／入 S6 凍結預測嘅實力分。
  · 唔覆蓋 elo_s2 參數、唔改 S6 凍結公式、唔把 ClubElo 機率當公開預測。
  · 對唔上名或當日冇 ClubElo 快照 → NULL，唔填 0、唔用均值頂。
  · 兩邊尺度唔同，所以按「聯賽內 z-score」比較，唔直接減 1500。

輸出：snapshots/clubelo_reconcile_YYYY-MM-DD.json（只增不改，已存在就 skip）

用法：
  python3 scripts/reconcile_elo.py              # 對帳未開賽賽程 + 近 14 日已完場
  python3 scripts/reconcile_elo.py --days 30 --force
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import math
import os
import statistics
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "data", "results")
FIXTURES = os.path.join(ROOT, "data", "fixtures", "upcoming.json")
DAILY_DIR = os.path.join(ROOT, "data", "clubelo", "daily")
MAPPING = os.path.join(ROOT, "mapping", "clubelo_names.csv")
SNAP_DIR = os.path.join(ROOT, "snapshots")

# 同 elo_s2.py / predict_fixtures.py 一致（已凍結，唔准喺呢度改）
MEAN, HFA, K0, REG = 1500.0, 60.0, 22.0, 0.70

# 告警起步線（兩週數據後再校）
MIN_COVERAGE = 0.95      # 一級隊對名成功率
MAX_MED_ZDIFF = 0.50     # 同隊 z-score 中位數差
MIN_SIGN_AGREE = 0.80    # 近 50 場 Elo 差符號一致率
MAX_DAILY_JUMP = 60.0    # 單日 ClubElo 跳幅（分）


def load_mapping() -> dict:
    m = {}
    with open(MAPPING, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            fd = (r.get("fd_name") or "").strip()
            ce = (r.get("clubelo_name") or "").strip()
            if fd and ce:
                m[fd] = {"clubelo": ce, "div": (r.get("fd_div") or "").strip(),
                         "country": (r.get("country") or "").strip(),
                         "level": (r.get("level") or "").strip()}
    return m


def load_history() -> list[dict]:
    rows = []
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "*.csv"))):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d = (r.get("Date") or "").strip()
                if len(d) < 8:
                    continue
                dd, mm, yy = d.split("/")
                yy = yy if len(yy) == 4 else ("20" + yy if int(yy) < 50 else "19" + yy)
                try:
                    gh, ga = int(float(r["FTHG"])), int(float(r["FTAG"]))
                except (ValueError, TypeError, KeyError):
                    continue
                if not r.get("HomeTeam") or not r.get("AwayTeam"):
                    continue
                rows.append({"iso": f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}",
                             "time": r.get("Time") or "", "div": r.get("Div", ""),
                             "home": r["HomeTeam"].strip(), "away": r["AwayTeam"].strip(),
                             "gh": gh, "ga": ga})
    rows.sort(key=lambda x: (x["iso"], x["time"], x["div"]))
    return rows


def season_of(y: int, m: int) -> int:
    return y if m >= 7 else y - 1


def self_elo_asof(hist: list[dict], target_dates: list[str]) -> dict[str, dict[str, float]]:
    """逐場前推，喺每個目標日期之前一剎影一次全隊綜合評分 0.5*(主評分+客評分)。"""
    hr, ar = defaultdict(lambda: MEAN), defaultdict(lambda: MEAN)
    last_season = {}
    snaps: dict[str, dict[str, float]] = {}
    pending = sorted(set(target_dates))
    i = 0

    def shoot(date: str) -> None:
        snaps[date] = {t: 0.5 * (hr[t] + ar[t]) for t in set(hr) | set(ar)}

    for m in hist:
        while i < len(pending) and pending[i] <= m["iso"]:
            shoot(pending[i])
            i += 1
        s = season_of(int(m["iso"][:4]), int(m["iso"][5:7]))
        for team in (m["home"], m["away"]):
            if last_season.get(team) not in (None, s):
                hr[team] = MEAN + REG * (hr[team] - MEAN)
                ar[team] = MEAN + REG * (ar[team] - MEAN)
            last_season[team] = s
        diff = (hr[m["home"]] + HFA) - ar[m["away"]]
        eh = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        res = m["gh"] - m["ga"]
        score_h = 1.0 if res > 0 else (0.5 if res == 0 else 0.0)
        gd = abs(res)
        km = 1.0 if gd <= 1 else (1.5 if gd == 2 else (1.75 if gd == 3 else 2.0))
        delta = K0 * km * (score_h - eh)
        hr[m["home"]] += delta
        ar[m["away"]] -= delta
    while i < len(pending):
        shoot(pending[i])
        i += 1
    return snaps


def clubelo_days() -> list[str]:
    return sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(DAILY_DIR, "*.csv")))


def load_clubelo_day(date: str) -> dict[str, dict]:
    out = {}
    with open(os.path.join(DAILY_DIR, f"{date}.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            club = (r.get("Club") or "").strip()
            try:
                elo = float(r.get("Elo") or "")
            except ValueError:
                continue
            frm, to = (r.get("From") or "").strip(), (r.get("To") or "").strip()
            cur = out.get(club)
            in_range = bool(frm and to and frm <= date <= to)
            # 優先取 From/To 覆蓋當日嘅一行
            if cur is None or (in_range and not cur["in_range"]):
                out[club] = {"elo": elo, "in_range": in_range,
                             "country": (r.get("Country") or "").strip(),
                             "level": (r.get("Level") or "").strip()}
    return out


def zmap(values: dict[str, float]) -> dict[str, float]:
    vals = list(values.values())
    if len(vals) < 4:
        return {}
    mu = statistics.fmean(vals)
    sd = statistics.pstdev(vals)
    if sd < 1e-9:
        return {}
    return {k: (v - mu) / sd for k, v in values.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="回望已完場天數")
    ap.add_argument("--force", action="store_true", help="覆寫今日快照")
    args = ap.parse_args()

    today = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    out_path = os.path.join(SNAP_DIR, f"clubelo_reconcile_{today}.json")
    if os.path.exists(out_path) and not args.force:
        print(f"skip：{os.path.relpath(out_path, ROOT)} 已存在（只增不改）")
        return 0

    days = clubelo_days()
    mapping = load_mapping()
    report: dict = {
        "date": today,
        "role": "ClubElo 只作對帳尺；自建 elo_s2 為唯一入模實力分",
        "clubelo_days": len(days),
        "clubelo_latest": days[-1] if days else None,
        "mapping_rows": len(mapping),
        "alerts": [],
    }

    if not days:
        report["status"] = "skip"
        report["note"] = "未有 ClubElo 快照（採集器未成功跑過或上游掛），今日對帳 skip，冇寫任何假數"
        os.makedirs(SNAP_DIR, exist_ok=True)
        json.dump(report, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    # 目標場次：未開賽賽程 + 近 N 日已完場；只對帳有對照表覆蓋嘅聯賽（現時五大一級）
    hist = load_history()
    divs = {mp["div"] for mp in mapping.values() if mp["div"]}
    report["divs"] = sorted(divs)
    cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(days=args.days)).strftime("%Y-%m-%d")
    targets = [{"date": m["iso"], "div": m["div"], "home": m["home"], "away": m["away"],
                "kind": "finished", "res": ("home" if m["gh"] > m["ga"]
                                            else ("draw" if m["gh"] == m["ga"] else "away"))}
               for m in hist if m["iso"] >= cutoff and m["div"] in divs]
    if os.path.exists(FIXTURES):
        fx = json.load(open(FIXTURES, encoding="utf-8"))
        for f in fx.get("fixtures", []):
            ko = (f.get("kickoff_utc") or "")[:10]
            if ko and f.get("div") in divs:
                targets.append({"date": ko, "div": f.get("div", ""), "home": f["home"],
                                "away": f["away"], "kind": "upcoming", "res": None})
    report["targets"] = len(targets)

    snaps = self_elo_asof(hist, [t["date"] for t in targets])
    day_cache: dict[str, dict[str, dict]] = {}

    def clubelo_asof(date: str) -> tuple[str | None, dict[str, dict]]:
        usable = [d for d in days if d <= date] or [days[0]]
        d = usable[-1]
        if d not in day_cache:
            day_cache[d] = load_clubelo_day(d)
        return d, day_cache[d]

    rows = []
    unmapped: set[str] = set()
    unmatched: set[str] = set()
    seen_level1: set[tuple[str, str]] = set()
    z_diffs: dict[str, list[float]] = defaultdict(list)
    sign_rows: list[tuple[str, int, int]] = []

    for t in targets:
        as_of, table = clubelo_asof(t["date"])
        self_snap = snaps.get(t["date"], {})
        pair = {}
        for side in ("home", "away"):
            team = t[side]
            seen_level1.add((t["div"], team))
            mp = mapping.get(team)
            if not mp:
                unmapped.add(f"{t['div']}|{team}")
                pair[side] = {"self": None, "clubelo": None}
                continue
            ce = table.get(mp["clubelo"])
            if ce is None:
                unmatched.add(f"{t['div']}|{team}→{mp['clubelo']}")
            pair[side] = {"self": self_snap.get(team), "clubelo": ce["elo"] if ce else None,
                          "clubelo_name": mp["clubelo"],
                          "in_range": ce["in_range"] if ce else None}

        # 聯賽內 z-score（只用當日該聯賽對得上名嘅隊）
        div_teams = [n for n, mp in mapping.items() if mp["div"] == t["div"]]
        self_vals = {n: self_snap[n] for n in div_teams if n in self_snap}
        ce_vals = {n: table[mapping[n]["clubelo"]]["elo"] for n in div_teams
                   if mapping[n]["clubelo"] in table}
        zs, zc = zmap(self_vals), zmap(ce_vals)
        z_pair = {}
        for side in ("home", "away"):
            n = t[side]
            a, b = zs.get(n), zc.get(n)
            z_pair[side] = {"z_self": round(a, 3) if a is not None else None,
                            "z_clubelo": round(b, 3) if b is not None else None,
                            "z_diff": round(b - a, 3) if (a is not None and b is not None) else None}
            if a is not None and b is not None:
                z_diffs[t["div"]].append(abs(b - a))

        sh, sa = pair["home"]["self"], pair["away"]["self"]
        ch, ca = pair["home"]["clubelo"], pair["away"]["clubelo"]
        if None not in (sh, sa, ch, ca):
            s_sign = 0 if abs(sh - sa) < 1e-9 else (1 if sh > sa else -1)
            c_sign = 0 if abs(ch - ca) < 1e-9 else (1 if ch > ca else -1)
            sign_rows.append((t["date"], s_sign, c_sign))

        rows.append({"date": t["date"], "div": t["div"], "kind": t["kind"],
                     "home": t["home"], "away": t["away"], "as_of": as_of,
                     "self": {"home": round(sh, 1) if sh is not None else None,
                              "away": round(sa, 1) if sa is not None else None,
                              "diff": round(sh - sa, 1) if None not in (sh, sa) else None},
                     "clubelo": {"home": ch, "away": ca,
                                 "diff": round(ch - ca, 1) if None not in (ch, ca) else None},
                     "z": z_pair})

    # 覆蓋率
    total_slots = len(seen_level1)
    bad = len({s.split("→")[0] for s in unmatched} | unmapped)
    coverage = (total_slots - bad) / total_slots if total_slots else 0.0
    report["coverage"] = {"teams": total_slots, "matched": total_slots - bad,
                          "rate": round(coverage, 4)}
    report["unmapped"] = sorted(unmapped)
    report["unmatched"] = sorted(unmatched)
    if total_slots and coverage < MIN_COVERAGE:
        report["alerts"].append(f"對名成功率 {coverage:.2%} 低於 {MIN_COVERAGE:.0%}：查 mapping/clubelo_names.csv")

    # 水平（按聯賽 z-score 中位數差）
    levels = {d: round(statistics.median(v), 3) for d, v in z_diffs.items() if v}
    report["z_diff_median"] = levels
    for d, v in levels.items():
        if v > MAX_MED_ZDIFF:
            report["alerts"].append(f"{d} z-score 中位數差 {v} 超過 {MAX_MED_ZDIFF}：查自建更新漏場")

    # 方向（近 50 場已完場）
    sign_rows.sort(key=lambda x: x[0])
    last50 = [r for r in sign_rows][-50:]
    if last50:
        agree = sum(1 for _, a, b in last50 if a == b) / len(last50)
        report["sign_agreement"] = {"n": len(last50), "rate": round(agree, 4)}
        if len(last50) >= 20 and agree < MIN_SIGN_AGREE:
            report["alerts"].append(f"近 {len(last50)} 場 Elo 差符號一致率 {agree:.2%} 低於 {MIN_SIGN_AGREE:.0%}：查對名或自建漏場")
    else:
        report["sign_agreement"] = None

    # 突變（連續兩日 ClubElo 跳幅）
    jumps = []
    if len(days) >= 2:
        prev = load_clubelo_day(days[-2])
        cur = load_clubelo_day(days[-1])
        for club, v in cur.items():
            p = prev.get(club)
            if p and abs(v["elo"] - p["elo"]) > MAX_DAILY_JUMP:
                jumps.append({"club": club, "from": p["elo"], "to": v["elo"],
                              "delta": round(v["elo"] - p["elo"], 1)})
    report["jumps"] = sorted(jumps, key=lambda x: -abs(x["delta"]))[:20]
    if jumps:
        report["alerts"].append(f"{len(jumps)} 隊單日 ClubElo 跳幅超過 {MAX_DAILY_JUMP} 分：標記備查，唔跟住改 K")

    report["status"] = "alert" if report["alerts"] else "ok"
    report["matches"] = rows

    os.makedirs(SNAP_DIR, exist_ok=True)
    json.dump(report, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    head = {k: v for k, v in report.items() if k != "matches"}
    print(json.dumps(head, ensure_ascii=False, indent=1))
    print(f"寫入 {os.path.relpath(out_path, ROOT)}：{len(rows)} 場")
    return 3 if report["alerts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
