#!/usr/bin/env python3
"""S25 試驗 #1：DIBP 對角膨脹（p 為聯賽級常數）

凍結：λ_H / λ_A 生成式完全等同現行生產軌（全局 atk/dfn、gamma=0.12、lr=0.04、
regress=0.80、warm=40、rho=-0.05、逐聯賽 base）。主客分拆攻防已於 S23 否決並鎖死，
本試驗一分不動 λ 層，只改「聯合分佈」嘅對角質量。

DIBP：M' = (1 - p_d) · M + p_d · D，D 只蓋 0-2 球和（0-0 / 1-1 / 2-2），
權重取自本場基準矩陣對角形狀（歸一化），所以 p 係聯賽常數、唔逐場、唔跟近況走。

p_d 兩種取法（兩者都只用開賽前資訊）：
  mode="p_grid"   → 全局固定 p（掃描用）
  mode="p_league" → 逐聯賽期望缺口，展開窗（只用之前已完場）：
                     p_d = clip((f_draw - q_draw) / (1 - q_draw), 0, PCAP)

閘門：主閘 RPS、實際比分格 log-loss、ECE（1X2 十桶）；
副閘 大細 2.5 log-loss、對角總質量對經驗和局率、頭八格覆蓋。
"""
import csv, json, math, os
from collections import defaultdict

CSV = "/tmp/fb/Matches.csv"
MAXG = 10
EVAL_FROM = 2021
PCAP = 0.15


def season_of(y, m):
    return y if m >= 7 else y - 1


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


def dibp(M, p):
    """對角膨脹：只把質量搬去 0-0 / 1-1 / 2-2，形狀跟本場基準對角。"""
    if p <= 0:
        return M
    diag = [M[k][k] for k in range(3)]
    s = sum(diag)
    if s <= 0:
        return M
    w = [v / s for v in diag]
    out = [[v * (1 - p) for v in row] for row in M]
    for k in range(3):
        out[k][k] += p * w[k]
    return out


def derive(M):
    ph = pd_ = pa = over = 0.0
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
            if i + j > 2.5:
                over += p
            cells.append((f"{i}-{j}", p))
    cells.sort(key=lambda c: -c[1])
    return ph, pd_, pa, over, cells


def rps3(p, k):
    y = [0.0, 0.0, 0.0]
    y[k] = 1.0
    c = cy = s = 0.0
    for i in range(2):
        c += p[i]
        cy += y[i]
        s += (c - cy) ** 2
    return s / 2


def run(rows, label, *, mode="baseline", p_fixed=0.0,
        lr=0.04, rho=-0.05, gamma=0.12, regress=0.80, warm=40):
    atk = defaultdict(float)
    dfn = defaultdict(float)
    seen = defaultdict(int)
    base = defaultdict(lambda: math.log(1.35))
    last_season = {}
    # 逐聯賽展開窗（只累積已完場）：預測和機率總和、實際和局數、場數
    acc = defaultdict(lambda: [0.0, 0, 0])
    p_cur = defaultdict(float)

    n = 0
    s_rps = s_ll = cs_ll = ou_ll = 0.0
    top1 = top8 = draws = 0
    diag_mass = 0.0
    bins = defaultdict(lambda: [0, 0.0, 0])
    mode_mix = defaultdict(int)
    per_season = defaultdict(lambda: [0, 0.0])
    per_div = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0])

    for r in rows:
        d, h, a = r["div"], r["home"], r["away"]
        sea = r["sea"]
        if last_season.get(d) != sea:
            last_season[d] = sea
            for t in list(atk):
                atk[t] *= regress
                dfn[t] *= regress
            if mode == "p_league":
                q, dr, m = acc[d]
                if m >= 600:
                    qb = q / m
                    fb = dr / m
                    p_cur[d] = min(max((fb - qb) / max(1 - qb, 1e-6), 0.0), PCAP)

        lh = math.exp(base[d] + gamma + atk[h] - dfn[a])
        la = math.exp(base[d] - gamma + atk[a] - dfn[h])
        lh = min(max(lh, 0.15), 6.0)
        la = min(max(la, 0.15), 6.0)

        M0 = build_matrix(lh, la, rho)
        p_use = 0.0 if mode == "baseline" else (p_fixed if mode == "p_grid" else p_cur[d])
        M = dibp(M0, p_use)
        pH, pD, pA, pOver, cells = derive(M)

        # 展開窗統計（用基準機率，唔用膨脹後，避免自我餵飼）
        _, pD0, _, _, _ = derive(M0)
        acc[d][0] += pD0
        acc[d][1] += int(r["res"] == "D")
        acc[d][2] += 1

        if seen[h] >= warm and seen[a] >= warm and sea >= EVAL_FROM:
            k = {"H": 0, "D": 1, "A": 2}[r["res"]]
            p = (pH, pD, pA)
            actual = f"{r['gh']}-{r['ga']}"
            n += 1
            s_rps += rps3(p, k)
            s_ll += -math.log(max(p[k], 1e-12))
            cell = dict(cells).get(actual)
            floor = cells[7][1] / 2 if len(cells) > 7 else 1e-6
            cs_ll += -math.log(max(cell if cell else floor, 1e-12))
            names = [c[0] for c in cells[:8]]
            top1 += int(actual == names[0])
            top8 += int(actual in names)
            mode_mix[names[0]] += 1
            diag_mass += pD
            draws += int(r["res"] == "D")
            yo = 1 if r["gh"] + r["ga"] > 2.5 else 0
            ou_ll += -math.log(max(pOver if yo else 1 - pOver, 1e-12))
            per_season[sea][0] += 1
            per_season[sea][1] += rps3(p, k)
            pv = per_div[d]
            pv[0] += 1
            pv[1] += rps3(p, k)
            pv[2] += pD
            pv[3] += p_use
            pv[4] += int(r["res"] == "D")
            for i in range(3):
                b = min(9, int(p[i] * 10))
                bins[b][0] += 1
                bins[b][1] += p[i]
                bins[b][2] += int(k == i)

        eh, ea = r["gh"] - lh, r["ga"] - la
        atk[h] += lr * eh
        dfn[a] -= lr * eh
        atk[a] += lr * ea
        dfn[h] -= lr * ea
        for t in (h, a):
            atk[t] = min(max(atk[t], -1.2), 1.2)
            dfn[t] = min(max(dfn[t], -1.2), 1.2)
        base[d] += 0.002 * ((r["gh"] + r["ga"]) - (lh + la)) / max(lh + la, 0.5)
        seen[h] += 1
        seen[a] += 1

    tot = max(sum(v[0] for v in bins.values()), 1)
    ece = sum(v[0] * abs(v[1] - v[2]) / max(v[0], 1) for v in bins.values()) / tot
    return {
        "label": label,
        "mode": mode,
        "p": round(p_fixed, 4) if mode == "p_grid" else (
            {k: round(v, 4) for k, v in sorted(p_cur.items())} if mode == "p_league" else 0),
        "n": n,
        "rps": round(s_rps / n, 5),
        "logloss": round(s_ll / n, 5),
        "ece": round(ece, 5),
        "cs_cell_logloss": round(cs_ll / n, 4),
        "ou25_logloss": round(ou_ll / n, 5),
        "cs_top1": round(top1 / n, 4),
        "cs_top8": round(top8 / n, 4),
        "diag_mass_mean": round(diag_mass / n, 4),
        "draw_freq_actual": round(draws / n, 4),
        "mode_11_share": round(mode_mix.get("1-1", 0) / n, 4),
        "mode_draw_share": round(
            sum(c for s, c in mode_mix.items() if s.split("-")[0] == s.split("-")[1]) / n, 4),
        "per_season_rps": {str(s): round(v[1] / v[0], 5) for s, v in sorted(per_season.items())},
        "per_div": {k: {"n": v[0], "rps": round(v[1] / v[0], 5),
                        "diag": round(v[2] / v[0], 4), "p": round(v[3] / v[0], 4),
                        "draw_actual": round(v[4] / v[0], 4)}
                    for k, v in sorted(per_div.items())},
    }


def gates(c, b):
    main = {
        "rps": c["rps"] <= b["rps"],
        "cs_cell_logloss": c["cs_cell_logloss"] <= b["cs_cell_logloss"],
        "ece": c["ece"] <= b["ece"],
    }
    sub = {
        "ou25_logloss": c["ou25_logloss"] <= b["ou25_logloss"] + 1e-4,
        "diag_vs_actual": abs(c["diag_mass_mean"] - c["draw_freq_actual"])
        <= abs(b["diag_mass_mean"] - b["draw_freq_actual"]),
        "cs_top8": c["cs_top8"] >= b["cs_top8"] - 0.002,
    }
    season_ok = all(c["per_season_rps"][s] <= b["per_season_rps"][s] + 1e-5
                    for s in b["per_season_rps"])
    return main, sub, season_ok, all(main.values()) and all(sub.values()) and season_ok


def load():
    rows = []
    with open(CSV, newline="") as f:
        for r in csv.DictReader(f):
            y, m, _ = (int(x) for x in r["MatchDate"].split("-"))
            rows.append({"div": r["Division"], "home": r["HomeTeam"], "away": r["AwayTeam"],
                         "gh": int(r["FTHome"]), "ga": int(r["FTAway"]), "res": r["FTResult"],
                         "sea": season_of(y, m), "date": r["MatchDate"]})
    return rows


if __name__ == "__main__":
    rows = load()
    print(f"五大聯賽 {len(rows)} 場，評分季 ≥ {EVAL_FROM}（λ 層鎖死 = 現行生產軌）")
    b = run(rows, "生產基準（無 DIBP）")
    print(f"基準 RPS {b['rps']} 格LL {b['cs_cell_logloss']} ECE {b['ece']} "
          f"OU LL {b['ou25_logloss']} 對角 {b['diag_mass_mean']} 實際和 {b['draw_freq_actual']} "
          f"1-1眾數 {b['mode_11_share']}")

    trials = []
    for p in (0.01, 0.02, 0.03, 0.05, 0.08, 0.12):
        trials.append(run(rows, f"DIBP 全局 p={p:.2f}", mode="p_grid", p_fixed=p))
    trials.append(run(rows, "DIBP 逐聯賽 p（展開窗）", mode="p_league"))

    for c in trials:
        main, sub, sea_ok, ok = gates(c, b)
        c["gate_main"], c["gate_sub"], c["gate_season"], c["gate_pass"] = main, sub, sea_ok, ok
        print(f"{c['label']:26s} RPS {c['rps']:.5f} 格LL {c['cs_cell_logloss']:.4f} "
              f"ECE {c['ece']:.5f} OU {c['ou25_logloss']:.5f} 對角 {c['diag_mass_mean']:.4f} "
              f"和眾數 {c['mode_draw_share']:.3f} → 主閘 {sum(main.values())}/3 "
              f"副閘 {sum(sub.values())}/3 逐季 {'過' if sea_ok else '不過'} "
              f"→ {'過' if ok else '不過'}")

    old = None
    for path in ("/tmp/fb/db/data/research/lambda_chain_result.json",):
        if os.path.exists(path):
            j = json.load(open(path))
            old = {"baseline": {k: j["baseline"].get(k) for k in
                                ("rps", "ece", "cs_cell_logloss", "params")},
                   "kappa_rows": [{"kappa": g["params"]["kappa"], "psi": g["params"]["psi"],
                                   "rps": g["rps"], "ece": g["ece"],
                                   "cs_cell_logloss": g["cs_cell_logloss"],
                                   "mode_11": g.get("mode_mix", {}).get("1-1")}
                                  for g in j.get("grid", [])]}
    json.dump({"note": "S25 試驗 #1 DIBP，研究軌；λ 層鎖死，未升指紋",
               "baseline": b, "trials": trials, "kappa_reference": old},
              open("/tmp/fb/s25_dibp.json", "w"), ensure_ascii=False, indent=1)
    print("寫入 /tmp/fb/s25_dibp.json")
