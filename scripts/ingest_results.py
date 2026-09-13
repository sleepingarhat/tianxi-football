"""自建賽果採集器 — 直接由 football-data.co.uk 官方 CSV 落地，唔靠第三方鏡射倉。

上游契約（同賽馬採集器同一套）：
  1. 用戶請求永不即時打上游；只由排程跑。
  2. 缺欄位留空，不准填假值。
  3. 逾時 + 有限重試；單一聯賽失敗唔會拖垮全run。
  4. 寫入可重複執行（同一檔覆蓋，內容穩定排序）。
  5. 抓取失敗保留上一份有效資料。
  6. 同步狀態、最後成功時間全部入 manifest 俾人查新鮮度。

用法：
  python3 scripts/ingest_results.py --out data/results [--seasons 2020:2026] [--divs E0,D1]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://www.football-data.co.uk/mmz4281"
UA = "tianxi-football-database/1.0 (+https://tianxi.racing)"

# 逐聯賽代碼 → 顯示名（港式）。football-data.co.uk 主要聯賽全覆蓋。
DIVS: dict[str, str] = {
    "E0": "英超", "E1": "英冠", "E2": "英甲", "E3": "英乙", "EC": "英足協全國聯賽",
    "SC0": "蘇超", "SC1": "蘇冠", "SC2": "蘇甲", "SC3": "蘇乙",
    "D1": "德甲", "D2": "德乙",
    "I1": "意甲", "I2": "意乙",
    "SP1": "西甲", "SP2": "西乙",
    "F1": "法甲", "F2": "法乙",
    "N1": "荷甲", "B1": "比甲", "P1": "葡超", "T1": "土超", "G1": "希超",
}

# 標準欄位：賽果層 + 賽前可得賠率（賠率只入 with_odds 對照軌，market_beta = 0）
KEEP = [
    "Div", "Date", "Time", "HomeTeam", "AwayTeam",
    "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR", "Referee",
    "HS", "AS", "HST", "AST", "HF", "AF", "HC", "AC", "HY", "AY", "HR", "AR",
    # odds-track only
    "B365H", "B365D", "B365A", "MaxH", "MaxD", "MaxA", "AvgH", "AvgD", "AvgA",
    "B365>2.5", "B365<2.5", "Avg>2.5", "Avg<2.5", "AHh", "AvgAHH", "AvgAHA",
]


def fetch(url: str, tries: int = 3, timeout: int = 45) -> bytes | None:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"user-agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 200:
                    raise urllib.error.HTTPError(url, r.status, "bad status", r.headers, None)
                return r.read()
        except Exception as e:  # noqa: BLE001 - 逐個聯賽獨立失敗
            if i == tries - 1:
                print(f"  ! fetch failed {url}: {e}", flush=True)
                return None
            time.sleep(2 * (i + 1))
    return None


def season_tag(start_year: int) -> str:
    """2025 → '2526'（football-data.co.uk 目錄格式）。"""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def norm_rows(raw: bytes, div: str, season: int) -> list[dict[str, str]]:
    text = raw.decode("utf-8-sig", errors="replace")
    rdr = csv.DictReader(io.StringIO(text))
    out: list[dict[str, str]] = []
    for row in rdr:
        if not (row.get("HomeTeam") and row.get("AwayTeam") and row.get("Date")):
            continue
        rec = {k: (row.get(k) or "").strip() for k in KEEP}
        rec["Div"] = rec["Div"] or div
        rec["season"] = str(season)
        rec["league_zh"] = DIVS.get(rec["Div"], "")
        out.append(rec)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/results")
    ap.add_argument("--seasons", default="2000:2026", help="起始年:結束年（賽季開季年）")
    ap.add_argument("--divs", default=",".join(DIVS))
    args = ap.parse_args()

    a, b = (int(x) for x in args.seasons.split(":"))
    divs = [d for d in args.divs.split(",") if d]
    os.makedirs(args.out, exist_ok=True)
    manifest_path = os.path.join(args.out, "_manifest.json")
    manifest: dict = {}
    if os.path.exists(manifest_path):
        manifest = json.load(open(manifest_path, encoding="utf-8"))
    files: dict = manifest.setdefault("files", {})

    ok_n = fail_n = rows_n = 0
    for season in range(a, b + 1):
        tag = season_tag(season)
        for div in divs:
            url = f"{BASE}/{tag}/{div}.csv"
            raw = fetch(url)
            key = f"{season}_{div}"
            path = os.path.join(args.out, f"{key}.csv")
            if raw is None:
                fail_n += 1
                # 契約第 5 條：保留上一份有效資料
                files.setdefault(key, {}).update(last_error=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))
                continue
            rows = norm_rows(raw, div, season)
            if not rows:
                fail_n += 1
                continue
            rows.sort(key=lambda r: (r["Date"], r["Time"], r["HomeTeam"]))
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=KEEP + ["season", "league_zh"])
                w.writeheader()
                w.writerows(rows)
            files[key] = dict(
                rows=len(rows), div=div, season=season, league_zh=DIVS.get(div, ""),
                last_success=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            )
            ok_n += 1
            rows_n += len(rows)
            print(f"  ok {key}: {len(rows)}", flush=True)
        time.sleep(0.5)  # 契約第 3 條：唔好轟上游

    manifest.update(
        source="football-data.co.uk (official CSV)",
        market_beta=0,
        note="賠率欄只入 with_odds 對照軌，永不進入排名主軌",
        last_run=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        last_run_ok=ok_n, last_run_fail=fail_n, last_run_rows=rows_n,
    )
    json.dump(manifest, open(manifest_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"done ok={ok_n} fail={fail_n} rows={rows_n}")
    # 全部失敗才當 run 失敗（單一聯賽斷唔算）
    return 1 if ok_n == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
