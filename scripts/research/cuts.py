#!/usr/bin/env python3
"""S19 研究軌：一刀一把掃描（殘差 → ρ(λ) → 逐聯賽 μ → 主客分拆攻防）

* 研究軌：唔改凍結預測、唔升指紋。每把刀獨立同基準比，要三閘齊過（RPS↓、實際格 log-loss↓、ECE ≤ 基準）先考慮寫入 S6。
* 嚴格時序前推：每場只用開賽前已迭代到嘅係數，賽後才更新。賠率零權重（market_beta = 0）。
* 五大聯賽（E0 D1 SP1 I1 F1）；同一張比分矩陣派生 1X2、波膽、大細、BTTS。

刀 1 殘差：res[t] = EMA(近十場 入球 − 當時 λ)，同 Elo／攻守正交（只食模型殘差，唔重複計實力）。
刀 2 ρ(λ)：rho_eff = rho0 · exp(−ψ · max(0, λh + λa − TOT0))
刀 3 μ_div：逐聯賽截距，唔用全局 log(1.35)
刀 4 主客分拆：atk/dfn 各分主客兩軌
刀 5 時間衰減 ξ：攻守學習率乘 exp(−ξ · 距今年數)（近場重大）
"""
import csv, glob, json, math, os
from collections import defaultdict, deque

BIG5 = ("E0", "D1", "SP1", "I1", "F1")
RESULTS = os.environ.get("TX_RESULTS", "/tmp/tfdb/data/results")
OUT = os.path.join(os.path.dirname(__file__))
MAXG = 10
EVAL_FROM = 2021
WARM = 40
MEAN, HFA, K0, REG = 1500.0, 60.0, 22.0, 0.70
TOT0 = 2.7


def season_of(y, m):
    return y if m >= 7 else y - 1


def load_rows():
    rows = []
    for path in sorted(glob.glob(os.path.join(RESULTS, "*.csv"))):
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("Div") not in BIG5:
                    continue
                try:
                    dd, mm, yy = (r.get("Date") or "").strip().split("/")
                    yy = int(yy)
                    yy = yy + 2000 if yy < 100 else yy
                    gh, ga = int(float(r["FTHG"])), int(float(r["FTAG"]))
                except (ValueError, KeyError, TypeError):
                    continue
                if r.get("FTR") not in ("H", "D", "A"):
                    continue
                rows.append({
                    "iso": f"{yy:04d}-{int(mm):02d}-{int(dd):02d}",
                    "y": yy, "m": int(mm),
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
    for (i, j), t in (((0, 0), 1 - lh * la * rho), ((0, 1), 1 + lh * rho),
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


def run(rows, label, *, kappa=0.0, psi=0.0, rho0=-0.05, lr=0.04, gamma=0.26,
        regress=0.80, per_league_mu=False, split_venue=False, w_res=0.0,
        res_n=10, xi=0.0):
    """一次回測。w_res > 0 開殘差刀；split_venue 開主客分拆；xi > 0 開時間衰減。"""
    atk = defaultdict(lambda: [0.0, 0.0])   # [home_track, away_track]（唔分拆時只用 [0]）
    dfn = defaultdict(lambda: [0.0, 0.0])
    seen = defaultdict(int)
    base = defaultdict(lambda: math.log(1.35))
    resq = defaultdict(lambda: deque(maxlen=res_n))   # 近 n 場 (入球 − λ) 殘差
    last_sea_div, last_sea_team = {}, {}
    hr, ar = defaultdict(lambda: MEAN), defaultdict(lambda: MEAN)
    last_y = max(r["y"] for r in rows)

    n = 0
    s_rps = s_ll = cs_ll = 0.0
    hit = top1 = top3 = top8 = 0
    bins = defaultdict(lambda: [0, 0.0, 0])
    mode_scores = defaultdict(int)
    per_season = defaultdict(lambda: [0, 0.0])

    def a_of(t, home):
        return atk[t][1] if (split_venue and not home) else atk[t][0]

    def d_of(t, home):
        return dfn[t][1] if (split_venue and not home) else dfn[t][0]

    for r in rows:
        d, h, a, sea = r["div"], r["home"], r["away"], r["sea"]
        if last_sea_div.get(d) != sea:
            last_sea_div[d] = sea
            for t in list(atk):
                atk[t] = [v * regress for v in atk[t]]
                dfn[t] = [v * regress for v in dfn[t]]
        for t in (h, a):
            if last_sea_team.get(t) is not None and last_sea_team[t] != sea:
                hr[t] = MEAN + REG * (hr[t] - MEAN)
                ar[t] = MEAN + REG * (ar[t] - MEAN)
            last_sea_team[t] = sea

        elo_diff = (hr[h] + HFA) - ar[a]
        mu = base[d] if per_league_mu else math.log(1.35)
        rh = sum(resq[h]) / len(resq[h]) if resq[h] else 0.0
        ra = sum(resq[a]) / len(resq[a]) if resq[a] else 0.0
        lh = math.exp(mu + gamma + a_of(h, True) - d_of(a, False)
                      + kappa * elo_diff / 400.0 + w_res * rh)
        la = math.exp(mu - gamma + a_of(a, False) - d_of(h, True)
                      - kappa * elo_diff / 400.0 + w_res * ra)
        lh = min(max(lh, 0.15), 6.0)
        la = min(max(la, 0.15), 6.0)
        rho = rho0 * math.exp(-psi * max(0.0, lh + la - TOT0))

        if seen[h] >= WARM and seen[a] >= WARM and sea >= EVAL_FROM:
            M = build_matrix(lh, la, rho)
            pH, pD, pA, cells = derive(M)
            k = {"H": 0, "D": 1, "A": 2}[r["res"]]
            p = (pH, pD, pA)
            actual = f"{r['gh']}-{r['ga']}"
            n += 1
            s_rps += rps3(p, k)
            s_ll += -math.log(max(p[k], 1e-12))
            hit += int(max(range(3), key=lambda i: p[i]) == k)
            per_season[sea][0] += 1
            per_season[sea][1] += rps3(p, k)
            cell_p = dict(cells).get(actual)
            floor = cells[7][1] / 2 if len(cells) > 7 else 1e-6
            cs_ll += -math.log(max(cell_p if cell_p else floor, 1e-12))
            names = [c[0] for c in cells[:8]]
            top1 += int(actual == names[0])
            top3 += int(actual in names[:3])
            top8 += int(actual in names)
            mode_scores[names[0]] += 1
            for i in range(3):
                b = min(9, int(p[i] * 10))
                bins[b][0] += 1
                bins[b][1] += p[i]
                bins[b][2] += int(k == i)

        eh_g, ea_g = r["gh"] - lh, r["ga"] - la
        decay = math.exp(-xi * max(0.0, last_y - r["y"])) if xi else 1.0
        step = lr * decay
        ih, ia = (0, 1) if split_venue else (0, 0)
        atk[h][ih] += step * eh_g
        dfn[a][ia] -= step * eh_g
        atk[a][ia] += step * ea_g
        dfn[h][ih] -= step * ea_g
        for t in (h, a):
            atk[t] = [min(max(v, -1.2), 1.2) for v in atk[t]]
            dfn[t] = [min(max(v, -1.2), 1.2) for v in dfn[t]]
        base[d] += 0.002 * ((r["gh"] + r["ga"]) - (lh + la)) / max(lh + la, 0.5)
        resq[h].append(eh_g)
        resq[a].append(ea_g)
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
    return {
        "label": label,
        "params": {"kappa": kappa, "psi": psi, "rho0": rho0, "per_league_mu": per_league_mu,
                   "split_venue": split_venue, "w_res": w_res, "res_n": res_n, "xi": xi},
        "n": n,
        "rps": round(s_rps / n, 5),
        "logloss": round(s_ll / n, 5),
        "accuracy": round(hit / n, 4),
        "ece": round(ece, 5),
        "cs_cell_logloss": round(cs_ll / n, 4),
        "cs_top1": round(top1 / n, 4),
        "cs_top8": round(top8 / n, 4),
        "mode_11_share": round(mode_scores.get("1-1", 0) / n, 4),
        "per_season_rps": {str(s): round(v[1] / v[0], 5) for s, v in sorted(per_season.items())},
    }


def gate(cand, base_):
    g = {
        "rps": cand["rps"] < base_["rps"],
        "cs_cell_logloss": cand["cs_cell_logloss"] < base_["cs_cell_logloss"],
        "ece": cand["ece"] <= base_["ece"],
    }
    return g, all(g.values())


if __name__ == "__main__":
    rows = load_rows()
    print(f"五大聯賽 {len(rows)} 場（評分季 ≥ {EVAL_FROM}）")
    b = run(rows, "baseline")
    print(f"基準 → RPS {b['rps']} 格LL {b['cs_cell_logloss']} ECE {b['ece']} "
          f"1-1眾數 {b['mode_11_share']}")

    trials = []
    for w in (0.15, 0.30, 0.50):
        trials.append(("刀1 殘差 w=%.2f" % w, {"w_res": w}))
    for psi in (0.6, 1.2, 2.0):
        trials.append(("刀2 ρ(λ) ψ=%.1f" % psi, {"psi": psi, "rho0": -0.10}))
    trials.append(("刀3 逐聯賽μ", {"per_league_mu": True}))
    trials.append(("刀4 主客分拆", {"split_venue": True}))
    for xi in (0.05, 0.12, 0.25):
        trials.append(("刀5 時間衰減 ξ=%.2f" % xi, {"xi": xi}))

    out = []
    for label, kw in trials:
        c = run(rows, label, **kw)
        g, ok = gate(c, b)
        c["gate"], c["gate_pass"] = g, ok
        out.append(c)
        print(f"{label:22s} RPS {c['rps']:.5f} 格LL {c['cs_cell_logloss']:.4f} "
              f"ECE {c['ece']:.5f} 1-1 {c['mode_11_share']:.3f} → 三閘 {'過' if ok else '不過'}")

    passers = [c for c in out if c["gate_pass"]]
    combo = None
    if passers:
        kw = {}
        for c in passers:
            for k, v in c["params"].items():
                if k in ("w_res", "psi", "rho0", "per_league_mu", "split_venue", "xi") and v:
                    kw[k] = v
        combo = run(rows, "合併過閘刀", **kw)
        g, ok = gate(combo, b)
        combo["gate"], combo["gate_pass"] = g, ok
        print(f"合併 {kw} → RPS {combo['rps']:.5f} 格LL {combo['cs_cell_logloss']:.4f} "
              f"ECE {combo['ece']:.5f} → 三閘 {'過' if ok else '不過'}")

    with open(os.path.join(OUT, "cuts_result.json"), "w", encoding="utf-8") as f:
        json.dump({"note": "研究軌，未入凍結預測、未升指紋",
                   "baseline": b, "trials": out, "combo": combo},
                  f, ensure_ascii=False, indent=1)
    print("寫入 research/cuts_result.json")
