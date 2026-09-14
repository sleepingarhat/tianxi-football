"""能力自述＋新鮮度自檢 — 俾看門狗自動核對，唔靠人手維護清單。

python3 scripts/selfcheck.py            # 人讀
python3 scripts/selfcheck.py --json     # 機讀（看門狗用；exit 1 = 有問題）
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os

RESULTS_MANIFEST = "data/results/_manifest.json"
FIXTURES_JSON = "data/fixtures/upcoming.json"
PREDICTIONS_JSON = "data/predictions/upcoming.json"
SNAP_DIR = "snapshots"
FIXTURES_MAX_AGE_H = 12
RESULTS_MAX_AGE_H = 30
PREDICTIONS_MAX_AGE_H = 12


def age_hours(iso: str) -> float | None:
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(iso)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.UTC)
    return (dt.datetime.now(dt.UTC) - t).total_seconds() / 3600


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    problems: list[str] = []
    rep: dict = {
        "repo": "tianxi-football-database",
        "market_beta": 0,
        "checked_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "capabilities": {
            "results": "football-data.co.uk 官方 CSV，2000 起 22 個聯賽，逐季一檔",
            "fixtures": "football-data.co.uk fixtures.csv，未來約一週賽程＋賽前平均賠率（odds-track）",
            "predictions": "predict_fixtures.py：逐場賽前凍結機率＋版本指紋（S3+S2 在線混合，LGB／集成推論待接）",
            "models": ["S2 天喜足球ELO", "S3 Dixon-Coles 入球模型", "S4 天喜足球LGB", "S5 三軌集成＋校準"],
            "labels": "只用 90 分鐘賽果，加時／點球唔入標籤",
            "clubelo": "api.clubelo.com 官方免 key CSV，每日快照＋as-of 對帳，只作外部尺（唔入模、唔入凍結預測）",
        },
    }

    # 賽果層
    if os.path.exists(RESULTS_MANIFEST):
        m = json.load(open(RESULTS_MANIFEST, encoding="utf-8"))
        files = m.get("files", {})
        total = sum(int(v.get("rows", 0)) for v in files.values())
        a = age_hours(m.get("last_run", ""))
        rep["results"] = dict(files=len(files), rows=total, last_run=m.get("last_run"),
                              age_hours=round(a, 1) if a is not None else None,
                              ok=m.get("last_run_ok"), fail=m.get("last_run_fail"))
        if a is not None and a > RESULTS_MAX_AGE_H:
            problems.append(f"賽果採集已 {a:.1f} 小時無成功（上限 {RESULTS_MAX_AGE_H}）")
        if not files:
            problems.append("賽果 manifest 為空")
    else:
        problems.append("賽果 manifest 唔存在（採集器未跑過）")

    # 賽程層
    if os.path.exists(FIXTURES_JSON):
        p = json.load(open(FIXTURES_JSON, encoding="utf-8"))
        meta = p.get("meta", {})
        a = age_hours(meta.get("last_success", ""))
        rep["fixtures"] = dict(count=meta.get("count"), leagues=len(meta.get("leagues", []) or []),
                               stale=bool(meta.get("stale")), last_success=meta.get("last_success"),
                               age_hours=round(a, 1) if a is not None else None)
        if meta.get("stale"):
            problems.append("賽程標記 stale（上游抓唔到，現用舊資料）")
        if a is not None and a > FIXTURES_MAX_AGE_H:
            problems.append(f"賽程已 {a:.1f} 小時無成功（上限 {FIXTURES_MAX_AGE_H}）")
        if not meta.get("count"):
            problems.append("賽程為零場")
    else:
        problems.append("賽程檔唔存在（採集器未跑過）")

    # 賽前預測層
    if os.path.exists(PREDICTIONS_JSON):
        pr = json.load(open(PREDICTIONS_JSON, encoding="utf-8"))
        meta = pr.get("meta", {})
        a = age_hours(meta.get("generated_at", ""))
        rep["predictions"] = dict(count=meta.get("fixtures_count"), engine=meta.get("engine"),
                                  fingerprint=meta.get("fingerprint"),
                                  history_matches=meta.get("history_matches"),
                                  generated_at=meta.get("generated_at"),
                                  age_hours=round(a, 1) if a is not None else None)
        if a is not None and a > PREDICTIONS_MAX_AGE_H:
            problems.append(f"賽前預測已 {a:.1f} 小時未重算（上限 {PREDICTIONS_MAX_AGE_H}）")
        if not meta.get("fixtures_count"):
            problems.append("賽前預測為零場")
    else:
        problems.append("賽前預測檔唔存在（predict_fixtures.py 未跑過）")

    # ClubElo 對帳層（只報告，唔當健康門檻：對帳源掛唔可以令公開預測算異常）
    ce_man = "data/clubelo/_manifest.json"
    ce_snaps = sorted(glob.glob(os.path.join(SNAP_DIR, "clubelo_reconcile_*.json")))
    ce = {"role": "外部對帳尺，唔入模型、唔入凍結預測", "reconcile_snapshots": len(ce_snaps)}
    if os.path.exists(ce_man):
        m = json.load(open(ce_man, encoding="utf-8"))
        ce.update(days=len(m.get("days", {})), last_success=m.get("last_success"),
                  last_success_date=m.get("last_success_date"), last_fail=m.get("last_fail"))
    if ce_snaps:
        r = json.load(open(ce_snaps[-1], encoding="utf-8"))
        ce.update(latest=os.path.basename(ce_snaps[-1]), status=r.get("status"),
                  coverage=r.get("coverage"), alerts=r.get("alerts"))
    rep["clubelo"] = ce

    # 凍結快照
    snaps = sorted(os.path.basename(p) for p in glob.glob(os.path.join(SNAP_DIR, "*.json")))
    rep["snapshots"] = snaps
    for need in ("elo_s2.json", "dc_s3.json", "lgb_s4.json", "ens_s5.json"):
        if need not in snaps:
            problems.append(f"缺凍結快照 {need}")

    rep["problems"] = problems
    rep["healthy"] = not problems

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        print("HEALTHY" if rep["healthy"] else "PROBLEMS: " + "; ".join(problems))
    return 0 if rep["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
