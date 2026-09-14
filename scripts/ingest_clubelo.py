#!/usr/bin/env python3
"""ClubElo 對帳層採集器（bronze）

只打官方免 key CSV API：http://api.clubelo.com/YYYY-MM-DD
欄位：Rank,Club,Country,Level,Elo,From,To

鐵律：
  · ClubElo 只做外部對帳尺，永不覆蓋 elo_s2 參數、永不入 S6 凍結預測。
  · 唔 scrape clubelo.com 網頁，只打官方 CSV API，限速 1 req/s。
  · 上游掛（502／超時）→ 保留舊快照、寫失敗記錄、非零退出俾看門狗開 issue，
    絕不寫假數、絕不填 0、絕不用均值頂。

用法：
  python3 scripts/ingest_clubelo.py                 # 抓今日全日表
  python3 scripts/ingest_clubelo.py --date 2026-09-14
  python3 scripts/ingest_clubelo.py --clubs "Man City,Paris SG"   # 補單隊歷史
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
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://api.clubelo.com"
DAILY_DIR = os.path.join(ROOT, "data", "clubelo", "daily")
CLUBS_DIR = os.path.join(ROOT, "data", "clubelo", "clubs")
MANIFEST = os.path.join(ROOT, "data", "clubelo", "_manifest.json")
HEADER = ["Rank", "Club", "Country", "Level", "Elo", "From", "To"]
UA = "tianxi-football-bot/1.0 (+https://tianxi.racing; reconciliation only)"
MIN_ROWS = 200          # 全日表正常有數千行，少過呢個當上游殘缺
RETRIES = 4
TIMEOUT = 45
RATE_SLEEP = 1.0        # 限速 1 req/s

_last_call = 0.0


def fetch(path: str) -> str:
    """限速＋重試抓一個 CSV 端點；全部失敗就拋例外（唔回假數）。"""
    global _last_call
    url = f"{BASE}/{urllib.parse.quote(path)}"
    last_err: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        gap = time.time() - _last_call
        if gap < RATE_SLEEP:
            time.sleep(RATE_SLEEP - gap)
        _last_call = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read().decode("utf-8", "replace")
            if not body.strip():
                raise ValueError("空回應")
            return body
        except Exception as exc:  # noqa: BLE001 上游任何故障都當可掛
            last_err = exc
            print(f"  第 {attempt} 次失敗：{exc}")
            if attempt < RETRIES:
                time.sleep(min(2 ** attempt, 12))
    raise RuntimeError(f"{url} 連續 {RETRIES} 次失敗：{last_err}")


def parse_table(body: str) -> list[dict]:
    rdr = csv.DictReader(io.StringIO(body))
    cols = [c.strip() for c in (rdr.fieldnames or [])]
    if cols[:len(HEADER)] != HEADER:
        raise ValueError(f"欄位唔對：{cols}")
    rows = []
    for r in rdr:
        club = (r.get("Club") or "").strip()
        elo = (r.get("Elo") or "").strip()
        if not club or not elo:
            continue
        try:
            float(elo)
        except ValueError:
            continue
        rows.append({k: (r.get(k) or "").strip() for k in HEADER})
    return rows


def write_csv(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)


def load_manifest() -> dict:
    if os.path.exists(MANIFEST):
        try:
            return json.load(open(MANIFEST, encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {"source": "api.clubelo.com（官方免 key CSV）", "role": "對帳尺，唔入模型",
            "days": {}, "clubs": {}}


def save_manifest(m: dict) -> None:
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    json.dump(m, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(dt.UTC).strftime("%Y-%m-%d"))
    ap.add_argument("--clubs", default="", help="逗號分隔 ClubElo 拼法隊名，補單隊歷史")
    args = ap.parse_args()

    now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    man = load_manifest()
    failed = False

    print(f"ClubElo 全日表 {args.date}")
    try:
        rows = parse_table(fetch(args.date))
        if len(rows) < MIN_ROWS:
            raise ValueError(f"只得 {len(rows)} 行，疑似上游殘缺（下限 {MIN_ROWS}）")
        path = os.path.join(DAILY_DIR, f"{args.date}.csv")
        write_csv(path, rows)
        man["days"][args.date] = {"rows": len(rows), "fetched_at": now}
        man["last_success"] = now
        man["last_success_date"] = args.date
        man.pop("last_fail", None)
        print(f"  寫入 {os.path.relpath(path, ROOT)}：{len(rows)} 行")
    except Exception as exc:  # noqa: BLE001
        failed = True
        man["last_fail"] = {"at": now, "date": args.date, "error": str(exc)[:400]}
        print(f"  失敗：{exc}；保留舊快照，當日對帳會 skip（唔寫假數）")

    for club in [c.strip() for c in args.clubs.split(",") if c.strip()]:
        print(f"ClubElo 隊史 {club}")
        try:
            rows = parse_table(fetch(club))
            slug = club.lower().replace(" ", "-").replace("/", "-")
            path = os.path.join(CLUBS_DIR, f"{slug}.csv")
            write_csv(path, rows)
            man["clubs"][club] = {"rows": len(rows), "fetched_at": now}
            print(f"  寫入 {os.path.relpath(path, ROOT)}：{len(rows)} 行")
        except Exception as exc:  # noqa: BLE001
            failed = True
            man.setdefault("club_fails", {})[club] = {"at": now, "error": str(exc)[:300]}
            print(f"  失敗：{exc}")

    save_manifest(man)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
