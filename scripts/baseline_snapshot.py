#!/usr/bin/env python3
"""天喜足球 S1 基準線快照 (baseline snapshot)

輸入：xgabora/Club-Football-Match-Data 的 Matches.csv（或 football-data.co.uk 原檔）
輸出：三條基準的 RPS / Brier / log-loss / ECE，逐聯賽逐賽季 + 總表，寫成 CSV + JSON

三條基準：
  A. uniform      —— 每類 1/3
  B. prior_asof   —— 只用「該聯賽在本場之前」的 H/D/A 頻率（walk-forward，嚴禁全期頻率）
  C. market_devig —— 開盤/收盤 1X2 賠率取倒數後歸一（去水）；只作對照，market_beta = 0

鐵律：
  - 不使用任何賽後欄位（射門/角球/牌/HT 比分）作基準特徵
  - 不使用 README 的 ExpectedGoals 欄（含賠率＋賽中統計，賽前不可得）
  - 嚴禁隨機切分，全部按 MatchDate 前推
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

CLASSES = ["H", "D", "A"]


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    df["MatchDate"] = pd.to_datetime(df["MatchDate"], errors="coerce")
    df = df.dropna(subset=["MatchDate", "FTResult", "Division"])
    df = df[df["FTResult"].isin(CLASSES)]
    # 賽季：7 月為界（歐洲賽季）
    df["Season"] = np.where(df["MatchDate"].dt.month >= 7,
                            df["MatchDate"].dt.year,
                            df["MatchDate"].dt.year - 1)
    df["Season"] = df["Season"].astype(int).astype(str) + "/" + (df["Season"] + 1).astype(str).str[-2:]
    return df.sort_values(["MatchDate"]).reset_index(drop=True)


def onehot(results: pd.Series) -> np.ndarray:
    idx = results.map({c: i for i, c in enumerate(CLASSES)}).to_numpy()
    out = np.zeros((len(idx), 3))
    out[np.arange(len(idx)), idx] = 1.0
    return out


def prior_asof(df: pd.DataFrame) -> np.ndarray:
    """逐聯賽 as-of 累積頻率；起步用 Laplace 平滑（每類 +1）。"""
    probs = np.full((len(df), 3), 1 / 3)
    for _, g in df.groupby("Division", sort=False):
        y = onehot(g["FTResult"])
        cum = np.cumsum(y, axis=0) - y  # 排除本場
        cum = cum + 1.0
        probs[g.index.to_numpy()] = cum / cum.sum(axis=1, keepdims=True)
    return probs


def market_devig(df: pd.DataFrame, cols=("OddHome", "OddDraw", "OddAway")) -> np.ndarray:
    o = df[list(cols)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / o
    bad = ~np.isfinite(inv).all(axis=1) | (o <= 1.0).any(axis=1)
    s = inv.sum(axis=1, keepdims=True)
    p = inv / s
    p[bad] = np.nan
    return p


def rps(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Ranked Probability Score（有序 H-D-A，主指標）。"""
    cp, cy = np.cumsum(p, axis=1), np.cumsum(y, axis=1)
    return ((cp[:, :-1] - cy[:, :-1]) ** 2).sum(axis=1) / (p.shape[1] - 1)


def metrics(p: np.ndarray, y: np.ndarray, bins: int = 10) -> dict:
    mask = np.isfinite(p).all(axis=1)
    p, y = p[mask], y[mask]
    if len(p) == 0:
        return {"n": 0}
    eps = 1e-15
    pc = np.clip(p, eps, 1 - eps)
    out = {
        "n": int(len(p)),
        "rps": float(rps(p, y).mean()),
        "brier": float(((p - y) ** 2).sum(axis=1).mean() / 2),
        "logloss": float(-np.log(pc[y == 1]).mean()),
        "acc": float((p.argmax(1) == y.argmax(1)).mean()),
    }
    edges = np.linspace(0, 1, bins + 1)
    for i, c in enumerate(CLASSES):
        idx = np.digitize(p[:, i], edges[1:-1])
        ece = 0.0
        for b in range(bins):
            m = idx == b
            if m.sum() == 0:
                continue
            ece += m.sum() / len(p) * abs(p[m, i].mean() - y[m, i].mean())
        out[f"ece_{c}"] = float(ece)
    out["ece_mean"] = float(np.mean([out[f"ece_{c}"] for c in CLASSES]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matches", default="data/Matches.csv")
    ap.add_argument("--outdir", default="snapshots")
    ap.add_argument("--divisions", default="", help="逗號分隔，例如 E0,SP1；留空 = 全部")
    args = ap.parse_args()

    df = load(Path(args.matches))
    if args.divisions:
        keep = [d.strip() for d in args.divisions.split(",") if d.strip()]
        df = df[df["Division"].isin(keep)].reset_index(drop=True)

    y = onehot(df["FTResult"])
    baselines = {
        "uniform": np.full((len(df), 3), 1 / 3),
        "prior_asof": prior_asof(df),
        "market_devig": market_devig(df),
    }

    rows = []
    for name, p in baselines.items():
        rows.append({"scope": "ALL", "division": "ALL", "season": "ALL",
                     "baseline": name, **metrics(p, y)})
        for div, g in df.groupby("Division"):
            i = g.index.to_numpy()
            rows.append({"scope": "division", "division": div, "season": "ALL",
                         "baseline": name, **metrics(p[i], y[i])})
        for (div, season), g in df.groupby(["Division", "Season"]):
            i = g.index.to_numpy()
            rows.append({"scope": "season", "division": div, "season": season,
                         "baseline": name, **metrics(p[i], y[i])})

    out = pd.DataFrame(rows)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    out.to_csv(outdir / "baseline_snapshot.csv", index=False)
    head = out[out.scope == "ALL"].to_dict("records")
    meta = {
        "source": "xgabora/Club-Football-Match-Data (MIT) ← football-data.co.uk",
        "matches": int(len(df)),
        "date_from": str(df["MatchDate"].min().date()),
        "date_to": str(df["MatchDate"].max().date()),
        "divisions": int(df["Division"].nunique()),
        "market_beta": 0,
        "overall": head,
    }
    (outdir / "baseline_snapshot.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
