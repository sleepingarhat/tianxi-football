#!/usr/bin/env python3
"""S5 集成推論訓練器（把回測軌變成可凍結嘅生產模型）。

流程：
  1. 用 scripts/features_s5.py 逐場前推自家 data/results，生成賽前特徵（絕不用賽後欄做特徵）。
  2. 逐季前推（walk-forward）出季外預測：S4 LGB 軌、S2 Elo 軌（多項邏輯）、S3 入球模型軌。
  3. 集成權重（對數空間 α 網格）同向量標度校準器，只用「測試季之前」嘅季外預測擬合。
  4. 三項閘門（RPS／log-loss／ECE 都要贏最佳單軌）過關才寫 ready = true；唔過就標 ready = false，
     凍結器會照舊退回 S3+S2 紅燈，唔會偷偷用未過閘嘅模型。
  5. 產出 models/s5/lgb.txt + models/s5/meta.json（含指紋、閘門報告、α、校準係數）。

鐵律：賠率零權重（market_beta = 0）；校準器同權重永不用測試季資料擬合。
"""
import hashlib, json, os, sys, warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import features_s5 as F

warnings.filterwarnings("ignore")
ROOT = F.ROOT
OUT_DIR = os.path.join(ROOT, "models", "s5")
EPS = 1e-9
GATE_SEASONS = 3          # 用最近三個完整賽季做閘門
FIT_SEASONS = 2           # α／校準器只用測試季之前兩季
PARAMS = dict(objective="multiclass", num_class=3, learning_rate=0.04, num_leaves=48,
              min_data_in_leaf=200, feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
              lambda_l2=2.0, verbose=-1, num_threads=4, seed=7)
ROUNDS = 450
LAB = {"H": 0, "D": 1, "A": 2}


def norm(P):
    P = np.clip(np.asarray(P, dtype=float), EPS, None)
    return P / P.sum(1, keepdims=True)


def rps(P, y):
    P = norm(P)
    Y = np.zeros_like(P)
    Y[np.arange(len(y)), y] = 1
    return float(np.mean(np.sum((np.cumsum(P[:, :2], 1) - np.cumsum(Y[:, :2], 1)) ** 2, 1) / 2))


def logloss(P, y):
    P = norm(P)
    return float(-np.mean(np.log(P[np.arange(len(y)), y])))


def ece(P, y, bins=10):
    P = norm(P)
    mp, hit = P.max(1), (P.argmax(1) == y).astype(float)
    b = np.clip((mp * bins).astype(int), 0, bins - 1)
    tot = 0.0
    for i in range(bins):
        m = b == i
        if m.sum() > 50:
            tot += abs(mp[m].mean() - hit[m].mean()) * m.sum()
    return float(tot / len(y))


def metrics(P, y):
    return dict(n=int(len(y)), rps=round(rps(P, y), 4), logloss=round(logloss(P, y), 4),
                acc=round(float(np.mean(norm(P).argmax(1) == y)), 4),
                draw_recall=round(float(np.mean(norm(P)[y == 1].argmax(1) == 1)), 4),
                ece=round(ece(P, y), 4))


def build():
    rows = F.load_history()
    div_ids = F.div_index(rows)
    eng = F.FeatureEngine()
    X, y, seasons, warm = [], [], [], []
    for m in rows:
        eng.roll_season(m["iso"], m["home"], m["away"])
        f = eng.features(m["iso"], m["div"], m["home"], m["away"], div_ids)
        X.append(eng.vector(f))
        ftr = "H" if m["gh"] > m["ga"] else ("D" if m["gh"] == m["ga"] else "A")
        y.append(LAB[ftr])
        seasons.append(F.season_of(m["iso"]))
        warm.append(eng.seen[m["home"]] >= F.WARM and eng.seen[m["away"]] >= F.WARM)
        eng.update(m)
    return (pd.DataFrame(X, columns=F.FEATURE_COLS), np.array(y), np.array(seasons),
            np.array(warm), rows, div_ids)


def softmax_params(lr):
    return {"coef": lr.coef_.tolist(), "intercept": lr.intercept_.tolist(),
            "classes": lr.classes_.tolist()}


def main():
    X, y, season, warm, rows, div_ids = build()
    print(f"history {len(X)} rows, {X.shape[1]} features, "
          f"{rows[0]['iso']} → {rows[-1]['iso']}", flush=True)

    cur = F.season_of(rows[-1]["iso"])
    done = sorted({int(s) for s in season if s < cur})
    test_seasons = done[-(GATE_SEASONS + FIT_SEASONS):]
    if len(test_seasons) < GATE_SEASONS + 1:
        print("ERROR: 完整賽季不足，無法過閘", file=sys.stderr)
        sys.exit(1)

    P_dc = norm(X[["dc_ph", "dc_pd", "dc_pa"]].values)
    P_elo = np.full((len(X), 3), np.nan)
    P_lgb = np.full((len(X), 3), np.nan)
    elo_cols = ["elo_diff", "elo_exp"]

    for s in test_seasons:
        tr = (season < s) & warm
        te = (season == s) & warm
        if tr.sum() < 20000 or te.sum() == 0:
            continue
        lr = LogisticRegression(max_iter=1000, C=1.0)
        lr.fit(X.loc[tr, elo_cols].values, y[tr])
        P_elo[te] = lr.predict_proba(X.loc[te, elo_cols].values)
        m = lgb.train(PARAMS, lgb.Dataset(X[tr], y[tr]), num_boost_round=ROUNDS)
        P_lgb[te] = m.predict(X[te])
        print(f"  季外預測 {s}: {int(te.sum())} 場", flush=True)

    have = ~np.isnan(P_lgb[:, 0]) & ~np.isnan(P_elo[:, 0]) & warm

    def blend(a, mask):
        L = (a[0] * np.log(np.clip(P_lgb[mask], EPS, None))
             + a[1] * np.log(np.clip(P_dc[mask], EPS, None))
             + a[2] * np.log(np.clip(P_elo[mask], EPS, None)))
        return norm(np.exp(L - L.max(1, keepdims=True)))

    GRID = [(l / 20, d / 20, round(1 - l / 20 - d / 20, 4))
            for l in range(8, 21) for d in range(0, 13)
            if -1e-9 <= 1 - l / 20 - d / 20 <= 0.5]

    gate_seasons = test_seasons[-GATE_SEASONS:]
    P_cal = np.full((len(X), 3), np.nan)
    per_season, last_fit = [], None
    for s in gate_seasons:
        te = (season == s) & have
        prev = [t for t in test_seasons if t < s][-FIT_SEASONS:]
        fit = np.isin(season, prev) & have
        if te.sum() == 0 or fit.sum() < 3000:
            continue
        alpha = min(GRID, key=lambda a: rps(blend(a, fit), y[fit]))
        Pf = blend(alpha, fit)
        vs = LogisticRegression(max_iter=2000, C=10.0)
        vs.fit(np.log(np.clip(Pf, EPS, None)), y[fit])
        Pe = blend(alpha, te)
        P_cal[te] = vs.predict_proba(np.log(np.clip(Pe, EPS, None)))
        per_season.append(dict(season=int(s), alpha=list(alpha), **metrics(P_cal[te], y[te])))
        print(f"  閘門季 {s}: {per_season[-1]}", flush=True)
        last_fit = (alpha, vs, [int(t) for t in prev])

    ok = have & ~np.isnan(P_cal[:, 0])
    if ok.sum() == 0 or last_fit is None:
        print("ERROR: 閘門季無有效預測", file=sys.stderr)
        sys.exit(1)
    yy = y[ok]
    report = dict(
        gate_seasons=[int(s) for s in gate_seasons],
        s5_calibrated=metrics(P_cal[ok], yy),
        tracks=dict(lgb_s4=metrics(P_lgb[ok], yy), dc_s3=metrics(P_dc[ok], yy),
                    elo_s2=metrics(P_elo[ok], yy)),
        per_season=per_season,
    )
    s5 = report["s5_calibrated"]
    best_rps = min(t["rps"] for t in report["tracks"].values())
    best_ll = min(t["logloss"] for t in report["tracks"].values())
    checks = {
        "rps": s5["rps"] < best_rps,
        "logloss": s5["logloss"] < best_ll,
        "ece": s5["ece"] <= 0.03,
    }
    ready = all(checks.values())
    report["gate"] = dict(passed=ready, checks=checks,
                          best_single_track_rps=best_rps, best_single_track_logloss=best_ll,
                          rule="RPS 同 log-loss 都要贏最佳單軌，且校準偏差 ECE ≤ 0.03，三項齊過才准入凍結軌")
    print(json.dumps(report["gate"], ensure_ascii=False, indent=1), flush=True)

    # --- 最終生產模型：用全部完整歷史重訓；α／校準器沿用閘門季之前擬合嘅一組 ---
    alpha, vscaler, fit_prev = last_fit
    fin = (season <= max(gate_seasons)) & warm
    final_lgb = lgb.train(PARAMS, lgb.Dataset(X[fin], y[fin]), num_boost_round=ROUNDS)
    final_elo = LogisticRegression(max_iter=1000, C=1.0)
    final_elo.fit(X.loc[fin, elo_cols].values, y[fin])

    os.makedirs(OUT_DIR, exist_ok=True)
    model_path = os.path.join(OUT_DIR, "lgb.txt")
    final_lgb.save_model(model_path)
    src = b"".join(open(os.path.join(ROOT, "scripts", n), "rb").read()
                   for n in ("features_s5.py", "train_s5.py"))
    fp = "-".join([
        hashlib.sha256(src).hexdigest()[:12],
        hashlib.sha256(open(model_path, "rb").read()).hexdigest()[:12],
        str(int(fin.sum())),
    ])
    meta = dict(
        version="s5.1",
        ready=ready,
        trained_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        fingerprint=fp,
        market_beta=0,
        feature_cols=F.FEATURE_COLS,
        elo_cols=elo_cols,
        div_ids=div_ids,
        alpha=dict(lgb=alpha[0], dc=alpha[1], elo=alpha[2]),
        alpha_fit_seasons=fit_prev,
        calibrator=dict(kind="vector_scaling", **softmax_params(vscaler)),
        elo_track=softmax_params(final_elo),
        train_rows=int(fin.sum()),
        train_span=[int(min(season)), int(max(gate_seasons))],
        history_rows=len(X),
        history_last_date=rows[-1]["iso"],
        warm_min=F.WARM,
        report=report,
    )
    json.dump(meta, open(os.path.join(OUT_DIR, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"寫入 {OUT_DIR}｜ready={ready}｜指紋 {fp}")


if __name__ == "__main__":
    main()
