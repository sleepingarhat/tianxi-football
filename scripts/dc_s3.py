#!/usr/bin/env python3
"""S3 天喜足球入球模型（Poisson / Dixon-Coles 在線版）

嚴格時序前推（walk-forward）：每場先用「開賽前」已迭代到嘅攻防係數出比分機率矩陣，
再用實際賽果做一次隨機梯度更新。禁止全歷史重擬合、禁止隨機切分、賠率零權重。

輸出：同一個比分機率矩陣派生 1X2 / 大細 2.5 / BTTS，保證各盤口互相一致。
"""
import csv, json, math, sys
from collections import defaultdict

CSV = "/tmp/fb/Matches.csv"
MAXG = 10  # 比分矩陣上限

def season_of(y, m):
    return y if m >= 7 else y - 1

def build_matrix(lh, la, rho):
    # 泊松邊際
    ph = [math.exp(-lh)]
    pa = [math.exp(-la)]
    for k in range(1, MAXG + 1):
        ph.append(ph[-1] * lh / k)
        pa.append(pa[-1] * la / k)
    M = [[ph[i] * pa[j] for j in range(MAXG + 1)] for i in range(MAXG + 1)]
    # Dixon-Coles 低比分修正
    tau = {(0, 0): 1 - lh * la * rho, (0, 1): 1 + lh * rho,
           (1, 0): 1 + la * rho, (1, 1): 1 - rho}
    for (i, j), t in tau.items():
        M[i][j] *= max(t, 1e-9)
    s = sum(sum(r) for r in M)
    return [[v / s for v in r] for r in M]

def derive(M):
    ph = pd_ = pa = 0.0
    over = 0.0
    btts = 0.0
    for i in range(MAXG + 1):
        for j in range(MAXG + 1):
            p = M[i][j]
            if i > j: ph += p
            elif i == j: pd_ += p
            else: pa += p
            if i + j > 2.5: over += p
            if i > 0 and j > 0: btts += p
    return ph, pd_, pa, over, btts

def rps(p, k):
    # p = (pH,pD,pA)；k = 0/1/2
    y = [0.0, 0.0, 0.0]; y[k] = 1.0
    c = 0.0; cy = 0.0; s = 0.0
    for i in range(2):
        c += p[i]; cy += y[i]
        s += (c - cy) ** 2
    return s / 2

def run(lr=0.04, rho=-0.05, gamma=0.12, regress=0.80, warm=40, decay=0.0, verbose=True):
    atk = defaultdict(float)   # log 攻擊係數
    dfn = defaultdict(float)   # log 防守係數
    seen = defaultdict(int)
    base = defaultdict(lambda: math.log(1.35))  # 逐聯賽基準入球率（log）
    last_season = {}

    n = 0; srps = 0.0; sll = 0.0; hit = 0
    ou_n = 0; ou_ll = 0.0; bt_ll = 0.0
    per_season = defaultdict(lambda: [0, 0.0, 0.0, 0])

    with open(CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: (r["MatchDate"], r.get("MatchTime") or ""))

    for r in rows:
        d = r["Division"]
        try:
            y, m, _ = (int(x) for x in r["MatchDate"].split("-"))
            gh = int(float(r["FTHome"])); ga = int(float(r["FTAway"]))
        except (ValueError, TypeError):
            continue
        res = r["FTResult"]
        if res not in ("H", "D", "A"):
            continue
        sea = season_of(y, m)
        # 跨季回歸：新賽季開始，係數向 0 收斂
        if last_season.get(d) != sea:
            last_season[d] = sea
            for t in list(atk):
                atk[t] *= regress; dfn[t] *= regress
        h, a = r["HomeTeam"], r["AwayTeam"]

        lh = math.exp(base[d] + gamma + atk[h] - dfn[a])
        la = math.exp(base[d] - gamma + atk[a] - dfn[h])
        lh = min(max(lh, 0.15), 6.0); la = min(max(la, 0.15), 6.0)

        ready = seen[h] >= warm and seen[a] >= warm
        if ready:
            M = build_matrix(lh, la, rho)
            pH, pD, pA, pOver, pBtts = derive(M)
            k = {"H": 0, "D": 1, "A": 2}[res]
            p = (pH, pD, pA)
            n += 1
            srps += rps(p, k)
            sll += -math.log(max(p[k], 1e-12))
            pred = max(range(3), key=lambda i: p[i])
            if pred == k: hit += 1
            ps = per_season[sea]
            ps[0] += 1; ps[1] += rps(p, k); ps[2] += -math.log(max(p[k], 1e-12)); ps[3] += int(pred == k)
            # 大細 2.5 / BTTS（同一矩陣派生）
            yo = 1 if gh + ga > 2.5 else 0
            ou_n += 1
            ou_ll += -math.log(max(pOver if yo else 1 - pOver, 1e-12))
            yb = 1 if gh > 0 and ga > 0 else 0
            bt_ll += -math.log(max(pBtts if yb else 1 - pBtts, 1e-12))

        # 賽後在線更新（泊松對數似然梯度）
        eh, ea = gh - lh, ga - la
        atk[h] += lr * eh; dfn[a] -= lr * eh
        atk[a] += lr * ea; dfn[h] -= lr * ea
        for t in (h, a):
            atk[t] = min(max(atk[t], -1.2), 1.2)
            dfn[t] = min(max(dfn[t], -1.2), 1.2)
        # 聯賽基準入球率慢速跟隨
        base[d] += 0.002 * ((gh + ga) - (lh + la)) / max(lh + la, 0.5)
        seen[h] += 1; seen[a] += 1

    out = {
        "matches_scored": n,
        "rps": srps / n, "logloss": sll / n, "accuracy": hit / n,
        "ou25_logloss": ou_ll / ou_n, "btts_logloss": bt_ll / ou_n,
        "params": {"lr": lr, "rho": rho, "home_gamma": gamma, "season_regress": regress, "warmup": warm},
    }
    if verbose:
        out["per_season"] = {
            str(s): {"n": v[0], "rps": v[1] / v[0], "logloss": v[2] / v[0], "acc": v[3] / v[0]}
            for s, v in sorted(per_season.items()) if v[0] > 200
        }
    return out

if __name__ == "__main__":
    main = run()
    print(json.dumps({k: v for k, v in main.items() if k != "per_season"}, indent=2))
    grid = []
    for lr in (0.025, 0.035, 0.045):
        for rho in (-0.03, -0.08):
            for gamma in (0.24, 0.32):
                g = run(lr=lr, rho=rho, gamma=gamma, verbose=False)
                grid.append(g)
                print(f"lr={lr} rho={rho} g={gamma} -> RPS {g['rps']:.4f} LL {g['logloss']:.4f} acc {g['accuracy']:.4f}")
    main["robustness"] = [{k: g[k] for k in ("rps", "logloss", "accuracy", "params")} for g in grid]
    json.dump(main, open("/tmp/fb/dc_s3.json", "w"), indent=2)
