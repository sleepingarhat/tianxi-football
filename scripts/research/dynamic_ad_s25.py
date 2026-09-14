#!/usr/bin/env python3
"""S25 試驗 #5：動態攻防（攻守兩條獨立隨機遊走、各自精度）

凍結：聯合分佈層（Dixon-Coles 低比分修正 rho=-0.05、主場項 gamma=0.12）
同閘門口徑完全鎖死，只改 λ_H / λ_A 嘅生成式——由固定學習率 SGD 轉做
每隊攻、守兩條獨立隨機遊走，各自帶精度（precision），賽後按 Fisher 信息更新。

狀態：
  atk[t], dfn[t]        — 隊 t 嘅攻、守 log 係數
  tau_atk[t], tau_dfn[t] — 對應精度（inverse variance）

預測步（as-of，只用開賽前資訊）：
  1. 按該隊上場日數加入過程噪聲 q：tau = 1 / (1/tau + q * days)
  2. 計 λ_H = exp(base[div] + gamma + atk[h] - dfn[a])
     λ_A = exp(base[div] - gamma + atk[a] - dfn[h])

更新步（賽後）：
  觀測資訊 = λ（Poisson log-link 嘅 Fisher 信息）
  atk[h]  += (gh - λ_H) / tau_atk[h]      ; tau_atk[h]  += λ_H
  dfn[a]  -= (gh - λ_H) / tau_dfn[a]      ; tau_dfn[a]  += λ_H
  atk[a]  += (ga - λ_A) / tau_atk[a]      ; tau_atk[a]  += λ_A
  dfn[h]  -= (ga - λ_A) / tau_dfn[h]      ; tau_dfn[h]  += λ_A

參數只准做全局常數（tau0 初始精度、q 過程噪聲、obs_scale 觀測資訊縮放），
唔准逐場、唔准跟近況走。跨季照舊向 0 收斂 regress=0.80。

閘門：主閘 RPS、實際比分格 log-loss、ECE（1X2 十桶）；
副閘 大細 2.5 log-loss、對角總質量對經驗和局率、頭八格覆蓋。
"""
import csv, json, math, os
from collections import defaultdict
from datetime import datetime, date

CSV = "/tmp/fb/Matches.csv"
MAXG = 10
EVAL_FROM = 2021


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


def run(rows, label, *, dynamic=False, tau0=1.0, q=0.0, obs_scale=1.0,
        lr=0.04, rho=-0.05, gamma=0.12, regress=0.80, warm=40):
    atk = defaultdict(float)
    dfn = defaultdict(float)
    tau_atk = defaultdict(float)
    tau_dfn = defaultdict(float)
    seen = defaultdict(int)
    last_date = {}
    base = defaultdict(lambda: math.log(1.35))
    last_season = {}

    n = s_rps = s_ll = cs_ll = ou_ll = 0.0
    top1 = top8 = draws = 0
    diag_mass = 0.0
    bins = defaultdict(lambda: [0, 0.0, 0])
    mode_mix = defaultdict(int)
    per_season = defaultdict(lambda: [0, 0.0])
    per_div = defaultdict(lambda: [0, 0.0, 0.0])

    for r in rows:
        d, h, a = r["div"], r["home"], r["away"]
        sea = r["sea"]
        cur_date = r["date_obj"]

        if last_season.get(d) != sea:
            last_season[d] = sea
            for t in list(atk):
                atk[t] *= regress
                dfn[t] *= regress

        # 過程噪聲：按距離上場日數增加不確定性（只對今場會用到嘅隊做）
        if dynamic:
            for t, dt in ((h, cur_date), (a, cur_date)):
                if t in last_date:
                    days = max(1, (dt - last_date[t]).days)
                    if q > 0 and tau_atk[t] > 0:
                        tau_atk[t] = 1.0 / (1.0 / tau_atk[t] + q * days)
                    if q > 0 and tau_dfn[t] > 0:
                        tau_dfn[t] = 1.0 / (1.0 / tau_dfn[t] + q * days)

        lh = math.exp(base[d] + gamma + atk[h] - dfn[a])
        la = math.exp(base[d] - gamma + atk[a] - dfn[h])
        lh = min(max(lh, 0.15), 6.0)
        la = min(max(la, 0.15), 6.0)

        if seen[h] >= warm and seen[a] >= warm and sea >= EVAL_FROM:
            M = build_matrix(lh, la, rho)
            pH, pD, pA, pOver, cells = derive(M)
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
            for i in range(3):
                b = min(9, int(p[i] * 10))
                bins[b][0] += 1
                bins[b][1] += p[i]
                bins[b][2] += int(k == i)

        eh, ea = r["gh"] - lh, r["ga"] - la
        if dynamic:
            # 初始化未見過隊伍嘅精度
            for t in (h, a):
                if tau_atk[t] <= 0:
                    tau_atk[t] = tau0
                if tau_dfn[t] <= 0:
                    tau_dfn[t] = tau0
            atk[h] += eh / tau_atk[h] * obs_scale
            tau_atk[h] += obs_scale * lh
            dfn[a] -= eh / tau_dfn[a] * obs_scale
            tau_dfn[a] += obs_scale * lh
            atk[a] += ea / tau_atk[a] * obs_scale
            tau_atk[a] += obs_scale * la
            dfn[h] -= ea / tau_dfn[h] * obs_scale
            tau_dfn[h] += obs_scale * la
        else:
            atk[h] += lr * eh
            dfn[a] -= lr * eh
            atk[a] += lr * ea
            dfn[h] -= lr * ea

        for t in (h, a):
            atk[t] = min(max(atk[t], -1.2), 1.2)
            dfn[t] = min(max(dfn[t], -1.2), 1.2)
            last_date[t] = cur_date

        base[d] += 0.002 * ((r["gh"] + r["ga"]) - (lh + la)) / max(lh + la, 0.5)
        seen[h] += 1
        seen[a] += 1

    tot = max(sum(v[0] for v in bins.values()), 1)
    ece = sum(v[0] * abs(v[1] - v[2]) / max(v[0], 1) for v in bins.values()) / tot
    return {
        "label": label,
        "dynamic": dynamic,
        "params": {"tau0": tau0, "q": q, "obs_scale": obs_scale,
                   "lr": lr, "rho": rho, "gamma": gamma, "regress": regress, "warm": warm},
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
        "per_div": {k: {"n": v[0], "rps": round(v[1] / v[0], 5), "diag": round(v[2] / v[0], 4)}
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
            y, m, day = (int(x) for x in r["MatchDate"].split("-"))
            rows.append({
                "div": r["Division"],
                "home": r["HomeTeam"],
                "away": r["AwayTeam"],
                "gh": int(float(r["FTHome"])),
                "ga": int(float(r["FTAway"])),
                "res": r["FTResult"],
                "sea": season_of(y, m),
                "date": r["MatchDate"],
                "date_obj": date(y, m, day),
            })
    rows.sort(key=lambda r: (r["date_obj"], r.get("time") or "", r["home"]))
    return rows


if __name__ == "__main__":
    rows = load()
    print(f"五大聯賽 {len(rows)} 場，評分季 ≥ {EVAL_FROM}")
    b = run(rows, "生產基準（固定 lr SGD）", dynamic=False)
    print(f"基準 RPS {b['rps']} 格LL {b['cs_cell_logloss']} ECE {b['ece']} "
          f"OU LL {b['ou25_logloss']} 對角 {b['diag_mass_mean']} 實際和 {b['draw_freq_actual']} "
          f"1-1眾數 {b['mode_11_share']}")

    trials = []
    # 先粗掃：tau0 控制初始步長，q 控制適應速度
    for tau0 in (5.0, 10.0, 25.0):
        for q in (0.0, 0.0001, 0.0005, 0.001):
            for obs in (0.5, 1.0):
                label = f"動態 tau0={tau0:.1f} q={q:.4f} obs={obs:.1f}"
                trials.append(run(rows, label, dynamic=True, tau0=tau0, q=q, obs_scale=obs))

    for c in trials:
        main, sub, sea_ok, ok = gates(c, b)
        c["gate_main"], c["gate_sub"], c["gate_season"], c["gate_pass"] = main, sub, sea_ok, ok
        print(f"{c['label']:42s} RPS {c['rps']:.5f} 格LL {c['cs_cell_logloss']:.4f} "
              f"ECE {c['ece']:.5f} OU {c['ou25_logloss']:.5f} 對角 {c['diag_mass_mean']:.4f} "
              f"和眾數 {c['mode_draw_share']:.3f} → 主閘 {sum(main.values())}/3 "
              f"副閘 {sum(sub.values())}/3 逐季 {'過' if sea_ok else '不過'} "
              f"→ {'過' if ok else '不過'}")

    best = min(trials, key=lambda c: (c["rps"], c["cs_cell_logloss"], c["ece"]))
    json.dump({
        "note": "S25 試驗 #5 動態攻防，研究軌；聯合分佈層鎖死，只改 λ 生成式，未升指紋",
        "baseline": b,
        "trials": trials,
        "best_by_rps": best["label"],
    }, open("/tmp/fb/s25_dynamic_ad.json", "w"), ensure_ascii=False, indent=1)
    print("寫入 /tmp/fb/s25_dynamic_ad.json")
