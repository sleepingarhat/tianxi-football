#!/usr/bin/env python3
"""寫收料狀態檔 data/_status/<job>.json，畀主站「收料狀態頁」讀。只讀資料，唔改任何資料。
用法：write_status.py <job> <data_dir> <from> <to> <attempts> <ok|fail>"""
import csv, glob, json, os, sys
from datetime import datetime, timezone
job, ddir, f, t, att, res = sys.argv[1:7]
n = 0; by = {}
for p in glob.glob(f"{ddir}/*.csv"):
    for r in csv.DictReader(open(p, encoding="utf-8")):
        if f <= r.get("date", "") <= t and r.get("home_xg") not in (None, ""):
            n += 1; by[r["div"]] = by.get(r["div"], 0) + 1
os.makedirs("data/_status", exist_ok=True)
json.dump({"job": job, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "window": [f, t],
           "matches_with_xg": n, "by_div": by, "attempts": int(att), "result": res},
          open(f"data/_status/{job}.json", "w"), ensure_ascii=False, indent=1)
print(job, n, by, res)
