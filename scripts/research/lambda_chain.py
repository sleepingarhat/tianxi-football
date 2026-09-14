#!/usr/bin/env python3
"""S17 研究軌：λ 生成鏈掃描（Elo 差注入 λ ＋ ρ 隨強度衰減 ＋ 聯賽自己嘅 μ）

* 研究軌，唔改凍結預測、唔升指紋。要過三閘（RPS／實際格 log-loss／ECE）先另案考慮寫入 S6。
* 嚴格時序前推：每場只用開賽前已迭代到嘅 Elo 同攻防係數，賽後才更新。賠率零權重（market_beta = 0）。
* 五大聯賽（E0 D1 SP1 I1 F1）；同一張比分矩陣派生 1X2、波膽、大細、BTTS。

λ_h = exp(μ_div + γ + atk_h − dfn_a + κ · Δelo / 400)
ρ_eff = ρ0 · exp(−ψ · max(0, λ_h + λ_a − TOT0))   # 懸殊／高入球場關細低比分修正
"""
import csv, glob, json, math, os
from collections import defaultdict

BIG5 = ("E0", "D1", "SP1", "I1", "F1")
RESULTS = os.path.join(os.path.dirname(__file__), "..", "..", "data", "results")
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "research")
MAXG = 10
EVAL_FROM = 2021          # 之前賽季只用嚟熱身，唔入評分
WARM = 40                 # 兩隊各自最少場數才計入評分
# Elo（同 S2 天喜足球ELO 一致）
MEAN, HFA, K0, REG = 1500.0, 60.0, 22.0, 0.70
TOT0 = 2.7                # ρ 衰減起點（兩邊 λ 合計）


def season_of(y: int, m: int) -> int:
    return y if m >= 7 else y - 1


def load_rows():
    rows = []
    for path in sorted(glob.glob(os.path.join(RESULTS, "*.csv"))):
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("Div") not in BIG5:
                    continue
                d = (r.get("Date") or "").strip()
                try:
                    dd, mm, yy = d.split("/")
                    yy = int(yy)
                    yy = yy + 2000 if yy < 100 else yy
                    gh, ga = int(float(r["FTHG"])), int(float(r["FTAG"]))
                except (ValueError, KeyError, TypeError):
                    continue
                if r.get("FTR") not in ("H", "D", "A"):
                    continue
                rows.append({
                    "iso": f"{yy:04d}-{int(mm):02d}-{int(dd):02d}",
                    "div": r["Div"], "home": r["HomeTeam"], "away": r["AwayTeam"],
                    "gh": gh, "ga": ga, "res": r["FTR"], "sea": season_of(yy, int(mm)),
                })
    rows.sort(key=lambda x: (x["iso"], x["div"], x["home"]))
    return rows


def build_matrix(lh, la, rho):
    ph = [math.exp(-lh)]
    pa = [math.exp(-la)]
    for k in range(1, MAXG + 1):
        ph.append(ph[-1] * lh / k)
        pa.append(pa[-1] * la / k)
    M = [[ph[i] * pa[j] for j in range(MAXG + 1)] for i in range(MAXG + 1)]
    for (i, j), t in ((((0, 0)), 1 - lh * la * rho), ((0, 1), 1 + lh * rho),
                      ((1, 0), 1 + la * rho), ((1, 1), 1 - rho)):
        M[i][j] *= max(t, 1e-9)
    s = sum(sum(r) for r in M)
    return [[v / s for v in r] for r in M]


def derive(M):
    ph = pd_ = pa = 0.0
    cells = []
    for i in range(MAXG + 1):
        for j in range(MAXG + 1):
            p = M[i][j]
            if i > j:
                ph += p
            elif i == j:
                pd_ += p
            else:
                pa += p
            cells.append((f"{i}-{j}", p))
    cells.sort(key=lambda c: -c[1])
    return ph, pd_, pa, cells


def rps3(p, k):
    y = [0.0, 0.0, 0.0]
    y[k] = 1.0
    c = cy = s = 0.0
    for i in range(2):
        c += p[i]
        cy += y[i]
        s += (c - cy) ** 2
    return s / 2


def run(rows, kappa=0.0, psi=0.0, rho0=-0.10, lr=0.04, gamma=0.26, regress=0.80,
        per_league_mu=True):
    atk, dfn, seen = defaultdict(float), defaultdict(float), defaultdict(int)
    base = defaultdict(lambda: math.log(1.35))
    last_sea_div, last_sea_team = {}, {}
    hr, ar = defaultdict(lambda: MEAN), defaultdict(lambda: MEAN)

    n = 0
    s_rps = s_ll = 0.0
    hit = 0
    cs_ll = 0.0
    top1 = top3 = top8 = 0
    bins = defaultdict(lambda: [0, 0.0, 0])       # 校準桶：n, sum_p, sum_y
    mode_scores = defaultdict(int)
    per_season = defaultdict(lambda: [0, 0.0])

    for r in rows:
        d, h, a, sea = r["div"], r["home"], r["away"], r["sea"]
        if last_sea_div.get(d) != sea:
            last_sea_div[d] = sea
            for t in list(atk):
                atk[t] *= regress
                dfn[t] *= regress
        for t in (h, a):
            if last_sea_team.get(t) is not None and last_sea_team[t] != sea:
                hr[t] = MEAN + REG * (hr[t] - MEAN)
                ar[t] = MEAN + REG * (ar[t] - MEAN)
            last_sea_team[t] = sea

        elo_diff = (hr[h] + HFA) - ar[a]
        mu = base[d] if per_league_mu else math.log(1.35)
        lh = math.exp(mu + gamma + atk[h] - dfn[a] + kappa * elo_diff / 400.0)
        la = math.exp(mu - gamma + atk[a] - dfn[h] - kappa * elo_diff / 400.0)
        lh = min(max(lh, 0.15), 6.0)
        la = min(max(la, 0.15), 6.0)
        rho = rho0 * math.exp(-psi * max(0.0, lh + la - TOT0))

        ready = seen[h] >= WARM and seen[a] >= WARM and sea >= EVAL_FROM
        if ready:
            M = build_matrix(lh, la, rho)
            pH, pD, pA, cells = derive(M)
            k = {"H": 0, "D": 1, "A": 2}[r["res"]]
            p = (pH, pD, pA)
            actual = f"{r['gh']}-{r['ga']}"
            n += 1
            s_rps += rps3(p, k)
            s_ll += -math.log(max(p[k], 1e-12))
            pred = max(range(3), key=lambda i: p[i])
            hit += int(pred == k)
            per_season[sea][0] += 1
            per_season[sea][1] += rps3(p, k)
            # 波膽：實際格 log-loss（跌出頭八格用頭八格最細機率一半罰分）
            cell_p = dict(cells).get(actual)
            floor = cells[7][1] / 2 if len(cells) > 7 else 1e-6
            cs_ll += -math.log(max(cell_p if cell_p else floor, 1e-12))
            names = [c[0] for c in cells[:8]]
            top1 += int(actual == names[0])
            top3 += int(actual in names[:3])
            top8 += int(actual in names)
            mode_scores[names[0]] += 1
            # 1X2 校準（十桶）
            for i in range(3):
                b = min(9, int(p[i] * 10))
                bins[b][0] += 1
                bins[b][1] += p[i]
                bins[b][2] += int(k == i)

        eh_g, ea_g = r["gh"] - lh, r["ga"] - la
        atk[h] += lr * eh_g
        dfn[a] -= lr * eh_g
        atk[a] += lr * ea_g
        dfn[h] -= lr * ea_g
        for t in (h, a):
            atk[t] = min(max(atk[t], -1.2), 1.2)
            dfn[t] = min(max(dfn[t], -1.2), 1.2)
        base[d] += 0.002 * ((r["gh"] + r["ga"]) - (lh + la)) / max(lh + la, 0.5)
        seen[h] += 1
        seen[a] += 1

        gd = r["gh"] - r["ga"]
        e_h = 1.0 / (1.0 + 10 ** (-elo_diff / 400.0))
        sc = 1.0 if gd > 0 else (0.5 if gd == 0 else 0.0)
        km = 1.0 if abs(gd) <= 1 else (1.5 if abs(gd) == 2 else (1.75 if abs(gd) == 3 else 2.0))
        delta = K0 * km * (sc - e_h)
        hr[h] += delta
        ar[a] -= delta

    ece = sum(v[0] * abs(v[1] - v[2]) / max(v[0], 1) for v in bins.values()) / max(
        sum(v[0] for v in bins.values()), 1)
    top_mode = sorted(mode_scores.items(), key=lambda kv: -kv[1])[:6]
    return {
        "params": {"kappa": kappa, "psi": psi, "rho0": rho0, "lr": lr, "gamma": gamma,
                   "per_league_mu": per_league_mu, "eval_from": EVAL_FROM},
        "n": n,
        "rps": round(s_rps / n, 5),
        "logloss": round(s_ll / n, 5),
        "accuracy": round(hit / n, 4),
        "ece": round(ece, 5),
        "cs_cell_logloss": round(cs_ll / n, 4),
        "cs_top1": round(top1 / n, 4),
        "cs_top3": round(top3 / n, 4),
        "cs_top8": round(top8 / n, 4),
        "mode_mix": {s: round(c / n, 4) for s, c in top_mode},
        "per_season_rps": {str(s): round(v[1] / v[0], 5) for s, v in sorted(per_season.items())},
    }


if __name__ == "__main__":
    rows = load_rows()
    print(f"五大聯賽場次 {len(rows)}（評分季 ≥ {EVAL_FROM}）")
    grid = []
    baseline = run(rows, kappa=0.0, psi=0.0, rho0=-0.05)
    print("基準（κ=0, ψ=0, ρ=−0.05）：", json.dumps(
        {k: baseline[k] for k in ("n", "rps", "logloss", "ece", "cs_cell_logloss", "cs_top8")},
        ensure_ascii=False))
    for kappa in (0.0, 0.15, 0.30, 0.45, 0.60):
        for psi in (0.0, 0.6, 1.2):
            g = run(rows, kappa=kappa, psi=psi, rho0=-0.10)
            grid.append(g)
            print(f"κ={kappa:.2f} ψ={psi:.1f} → RPS {g['rps']:.5f} 格LL {g['cs_cell_logloss']:.4f} "
                  f"ECE {g['ece']:.5f} Top8 {g['cs_top8']:.3f} 1-1眾數 {g['mode_mix'].get('1-1', 0):.3f}")
    best = min(grid, key=lambda g: (g["rps"], g["cs_cell_logloss"]))
    gate = {
        "rps": best["rps"] < baseline["rps"],
        "cs_cell_logloss": best["cs_cell_logloss"] < baseline["cs_cell_logloss"],
        "ece": best["ece"] <= 0.03,
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "lambda_chain.json"), "w", encoding="utf-8") as f:
        json.dump({"note": "研究軌，未入凍結預測、未升指紋",
                   "baseline": baseline, "grid": grid, "best": best,
                   "gate": gate, "gate_pass": all(gate.values())}, f,
                  ensure_ascii=False, indent=1)
    print("最佳：", json.dumps(best["params"], ensure_ascii=False),
          "→ 三閘", json.dumps(gate, ensure_ascii=False))
