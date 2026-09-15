#!/usr/bin/env python3
"""S13 逐場凍結帳（prediction_log）— 只增不改

由 data/predictions/upcoming.json（未開窗口，每日覆寫）upsert 落逐月凍結帳
data/predictions/log/YYYY-MM.json（按 match_key 一場一行）。

鐵律：
  · 一經鎖定（locked_at 已寫）嘅場次，預測欄（p／lambda／cs／fingerprint／track／status）
    永不覆寫。就算上游 upcoming.json 出咗新數，都只會記入 audit 並拒絕改帳。
  · 未鎖（黃燈）嘅場次可以刷新，刷新即覆寫預測欄並更新 updated_at。
  · 賽果欄由 scripts/settle_predictions.py 另外補，本腳本永不寫賽果。
  · upcoming.json 只係未開窗口，唔係歷史 SSOT；歷史 SSOT 係本帳。
"""
import json, os, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "predictions", "upcoming.json")
LOG_DIR = os.path.join(ROOT, "data", "predictions", "log")
AUDIT = os.path.join(LOG_DIR, "audit.jsonl")

PRED_FIELDS = ("p", "lambda", "over25", "btts", "cs", "scores", "top_score",
               "elo_diff", "track", "status", "fingerprint", "engine")

# 鎖定政策（三份文件唯一口徑）：開賽前 60 分鐘由黃燈（可刷新）轉綠燈（已鎖）。
# 已鎖場次永遠跟當時指紋：重訓／升版只可影響之後未鎖嘅場次。
LOCK_POLICY = {
    "lock_minutes": 60,
    "rule": "開賽前 60 分鐘鎖定；已鎖場次永遠跟當時指紋，重訓只影響之後未鎖場次",
    "on_new_model": "拒絕改帳，只寫 log/audit.jsonl（action=refuse_locked / fingerprint_drift）",
}


def month_of(rec):
    ko = rec.get("kickoff_utc") or ""
    return ko[:7] if len(ko) >= 7 else datetime.now(timezone.utc).strftime("%Y-%m")


def load_month(month):
    path = os.path.join(LOG_DIR, f"{month}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return {"meta": {"month": month, "note": "逐場凍結帳，只增不改；賽果欄由結算腳本補"},
            "matches": {}}


def audit(lines):
    if not lines:
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(AUDIT, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def main():
    src = json.load(open(SRC, encoding="utf-8"))
    meta = src.get("meta", {})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    months, logs = {}, []
    added = refreshed = locked_now = refused = drifted = 0

    for m in src.get("matches", []):
        month = month_of(m)
        if month not in months:
            months[month] = load_month(month)
        book = months[month]["matches"]
        key = m["match_key"]
        pred = {
            "p": m.get("p"), "lambda": m.get("lambda"), "over25": m.get("over25"),
            "btts": m.get("btts"), "cs": m.get("cs"), "scores": m.get("scores"),
            "top_score": m.get("top_score"), "elo_diff": m.get("elo_diff"),
            "track": m.get("track"), "status": m.get("status"),
            "fingerprint": meta.get("fingerprint"), "engine": meta.get("engine"),
        }
        base = {
            "match_key": key, "div": m.get("div"), "league_zh": m.get("league_zh"),
            "home": m.get("home"), "away": m.get("away"),
            "kickoff_utc": m.get("kickoff_utc"), "time_uk": m.get("time_uk"),
            "market": m.get("market"), "edge": m.get("edge"),
        }
        cur = book.get(key)
        if cur is None:
            rec = dict(base)
            rec.update(pred)
            rec["first_seen"] = now
            rec["updated_at"] = now
            ko = m.get("kickoff_utc") or ""
            late = False
            try:
                from datetime import datetime, timezone
                kt = datetime.fromisoformat(ko.replace("+00:00", "")).replace(tzinfo=timezone.utc)
                nw = datetime.fromisoformat(now.replace("+00:00", "")).replace(tzinfo=timezone.utc)
                late = kt <= nw  # 開賽後先至入帳：從未賽前公開，永唔入綠燈
            except ValueError:
                pass
            rec["late_ingest"] = late
            rec["locked_at"] = now if (m.get("locked") and not late) else None
            rec["lock_minutes"] = meta.get("lock_minutes")
            rec["result"] = None
            book[key] = rec
            added += 1
            if rec["locked_at"]:
                locked_now += 1
            logs.append({"ts": now, "action": "insert", "match_key": key,
                         "locked": bool(rec["locked_at"]), "fingerprint": pred["fingerprint"]})
            continue

        if cur.get("locked_at"):
            changed = [k for k in PRED_FIELDS if json.dumps(cur.get(k), sort_keys=True)
                       != json.dumps(pred.get(k), sort_keys=True)]
            if changed:
                refused += 1
                logs.append({"ts": now, "action": "refuse_locked", "match_key": key,
                             "fields": changed,
                             "kept_fingerprint": cur.get("fingerprint"),
                             "rejected_fingerprint": pred["fingerprint"]})
            if pred["fingerprint"] and cur.get("fingerprint") \
                    and pred["fingerprint"] != cur.get("fingerprint"):
                drifted += 1
                logs.append({"ts": now, "action": "fingerprint_drift", "match_key": key,
                             "kept_fingerprint": cur.get("fingerprint"),
                             "rejected_fingerprint": pred["fingerprint"],
                             "note": "已鎖場次拒絕升指紋（重訓只影響之後未鎖場次）"})
            continue

        # 黃燈：可以刷新
        cur.update({k: v for k, v in base.items() if v is not None or k in ("market", "edge")})
        cur.update(pred)
        cur["updated_at"] = now
        refreshed += 1
        if m.get("locked") and not cur.get("late_ingest"):
            cur["locked_at"] = now
            locked_now += 1
            logs.append({"ts": now, "action": "lock", "match_key": key,
                         "fingerprint": pred["fingerprint"]})
        else:
            logs.append({"ts": now, "action": "refresh", "match_key": key,
                         "fingerprint": pred["fingerprint"]})

    os.makedirs(LOG_DIR, exist_ok=True)
    for month, book in months.items():
        book["meta"]["updated_at"] = now
        book["meta"]["count"] = len(book["matches"])
        json.dump(book, open(os.path.join(LOG_DIR, f"{month}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1, sort_keys=True)
    audit(logs)

    report = {"generated_at": now, "months": sorted(months),
              "inserted": added, "refreshed": refreshed, "locked": locked_now,
              "refused_locked": refused, "refused_fingerprint_drift": drifted,
              "fingerprint": meta.get("fingerprint"),
              "lock_minutes": meta.get("lock_minutes"),
              "lock_policy": LOCK_POLICY}
    if meta.get("lock_minutes") not in (None, LOCK_POLICY["lock_minutes"]):
        print(f"WARN: upcoming.json lock_minutes={meta.get('lock_minutes')} "
              f"同鎖定政策 {LOCK_POLICY['lock_minutes']} 唔一致", file=sys.stderr)
        report["lock_minutes_mismatch"] = True
    snap_dir = os.path.join(ROOT, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    json.dump(report, open(os.path.join(snap_dir, f"lock_{now[:10]}.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if not src.get("matches"):
        print("WARN: upcoming.json 冇場次，凍結帳今日無新增", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
