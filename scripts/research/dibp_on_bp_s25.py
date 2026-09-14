#!/usr/bin/env python3
"""S25 試驗 #4：DIBP 對角膨脹接喺 BP 共享衝擊 λ₃ 之上（只改聯合分佈）

凍結：λ_H / λ_A 生成式完全等同現行生產軌（全局 atk/dfn、gamma=0.12、lr=0.04、
regress=0.80、warm=40、逐聯賽 base）。λ 層一分不動。

疊法（次序寫死）：獨立泊松 → BP 共享衝擊 λ₃ → DC 低分修正 rho → DIBP 對角膨脹 p。
λ₃ 同 p 都係常數（全局或逐聯賽），唔逐場、唔跟近況走。

閘門：主閘 RPS、實際比分格 log-loss、ECE（1X2 十桶）；
副閘 大細 2.5 log-loss、對角總質量對經驗和局率、頭八格覆蓋；逐季 walk-forward 唔准輸。
"""
import csv, json, math, os
from collections import defaultdict

CSV = "/tmp/fb/Matches.csv"
MAXG = 10
EVAL_FROM = 2021

_FACT = [math.factorial(k) for k in range(MAXG + 2)]
_COMB = [[math.comb(n, k) for k in range(MAXG + 1)] for n in range(MAXG + 1)]


def season_of(y, m):
    return y if m >= 7 else y - 1


def dc_tweak(M, lh, la, rho):
    if rho == 0:
        return M
    for (i, j), t in (((0, 0), 1 - lh * la * rho), ((0, 1), 1 + lh * rho),
                      ((1, 0), 1 + la * rho), ((1, 1), 1 - rho)):
        M[i][j] *= max(t, 1e-9)
    return M


def dibp(M, p):
    """對角膨脹：只把質量搬去 0-0 / 1-1 / 2-2，形狀跟本場矩陣對角。"""
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


def build_matrix(lh, la, rho, l3=0.0, p=0.0):
    l3 = min(l3, min(lh, la) * 0.95)
    if l3 <= 0:
        ph = [math.exp(-lh)]
        pa = [math.exp(-la)]
        for k in range(1, MAXG + 1):
            ph.append(ph[-1] * lh / k)
            pa.append(pa[-1] * la / k)
        M = [[ph[i] * pa[j] for j in range(MAXG + 1)] for i in range(MAXG + 1)]
    else:
        l1, l2 = lh - l3, la - l3
        c = math.exp(-(l1 + l2 + l3))
        r = l3 / (l1 * l2)
        M = [[0.0] * (MAXG + 1) for _ in range(MAXG + 1)]
        for x in range(MAXG + 1):
            for y in range(MAXG + 1):
                s = 0.0
                for k in range(min(x, y) + 1):
                    s += _COMB[x][k] * _COMB[y][k] * _FACT[k] * (r ** k)
                M[x][y] = c * (l1 ** x) / _FACT[x] * (l2 ** y) / _FACT[y] * s
    M = dc_tweak(M, lh, la, rho)
    s = sum(sum(row) for row in M)
    M = [[v / s for v in row] for row in M]
    M = dibp(M, p)
    s = sum(sum(row) for row in M)
    return [[v / s for v in row] for row in M]


def derive(M):
    ph = pd_ = pa = over = 0.0
    cells = []
    for i in range(MAXG + 1):
        for j in range(MAXG + 1):
            q = M[i][j]
            if i > j:
                ph += q
            elif i == j:
                pd_ += q
            else:
                pa += q
            if i + j > 2.5:
                over += q
            cells.append((f"{i}-{j}", q))
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


def run(rows, label, *, l3=0.0, p=0.0, l3_by_div=None, p_by_div=None, rho=-0.05,
        lr=0.04, gamma=0.12, regress=0.80, warm=40):
    atk = defaultdict(float)
    dfn = defaultdict(float)
    seen = defaultdict(int)
    base = defaultdict(lambda: math.log(1.35))
    last_season = {}

    n = 0
    s_rps = s_ll = cs_ll = ou_ll = 0.0
    top1 = top8 = draws = 0
    diag_mass = 0.0
    bins = defaultdict(lambda: [0, 0.0, 0])
    mode_mix = defaultdict(int)
    per_season = defaultdict(lambda: [0, 0.0])

    for r in rows:
        d, h, a = r["div"], r["home"], r["away"]
        sea = r["sea"]
        if last_season.get(d) != sea:
            last_season[d] = sea
            for t in list(atk):
                atk[t] *= regress
                dfn[t] *= regress

        lh = min(max(math.exp(base[d] + gamma + atk[h] - dfn[a]), 0.15), 6.0)
        la = min(max(math.exp(base[d] - gamma + atk[a] - dfn[h]), 0.15), 6.0)

        M = build_matrix(lh, la, rho,
                         (l3_by_div or {}).get(d, l3),
                         (p_by_div or {}).get(d, p))
        pH, pD, pA, pOver, cells = derive(M)

        if seen[h] >= warm and seen[a] >= warm and sea >= EVAL_FROM:
            k = {"H": 0, "D": 1, "A": 2}[r["res"]]
            pr = (pH, pD, pA)
            actual = f"{r['gh']}-{r['ga']}"
            n += 1
            s_rps += rps3(pr, k)
            s_ll += -math.log(max(pr[k], 1e-12))
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
            per_season[sea][1] += rps3(pr, k)
            for i in range(3):
                b = min(9, int(pr[i] * 10))
                bins[b][0] += 1
                bins[b][1] += pr[i]
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
        "lambda3": round(l3, 4),
        "p_diag": round(p, 4),
        "lambda3_by_div": l3_by_div,
        "p_by_div": p_by_div,
        "rho": rho,
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
    b = run(rows, "生產基準（獨立泊松 + DC rho=-0.05）")
    print(f"基準 RPS {b['rps']} 格LL {b['cs_cell_logloss']} ECE {b['ece']} "
          f"OU {b['ou25_logloss']} 對角 {b['diag_mass_mean']} 實際和 {b['draw_freq_actual']} "
          f"1-1眾數 {b['mode_11_share']}")

    trials = []
    # 試驗 2 最佳方向：純 BP（rho=0）λ₃=0.15 校準最好但格LL退；連 DC 嘅 λ₃ 細幅
    for l3, rho in ((0.02, -0.05), (0.06, -0.05), (0.15, 0.0)):
        for p in (0.005, 0.01, 0.02, 0.04):
            tag = f"BP λ₃={l3:.2f}{'+DC' if rho else '(rho=0)'} + DIBP p={p:.3f}"
            trials.append(run(rows, tag, l3=l3, p=p, rho=rho))
    # 逐聯賽常數：意甲／德甲／法甲和局較多
    trials.append(run(rows, "逐聯賽 λ₃ + 逐聯賽 p", rho=-0.05,
                      l3_by_div={"E0": 0.0, "SP1": 0.0, "D1": 0.02, "F1": 0.02, "I1": 0.04},
                      p_by_div={"E0": 0.0, "SP1": 0.0, "D1": 0.005, "F1": 0.005, "I1": 0.01}))

    for c in trials:
        main, sub, sea_ok, ok = gates(c, b)
        c["gate_main"], c["gate_sub"], c["gate_season"], c["gate_pass"] = main, sub, sea_ok, ok
        print(f"{c['label']:38s} RPS {c['rps']:.5f} 格LL {c['cs_cell_logloss']:.4f} "
              f"ECE {c['ece']:.5f} OU {c['ou25_logloss']:.5f} 對角 {c['diag_mass_mean']:.4f} "
              f"和眾數 {c['mode_draw_share']:.3f} → 主閘 {sum(main.values())}/3 "
              f"副閘 {sum(sub.values())}/3 逐季 {'過' if sea_ok else '不過'} "
              f"→ {'過' if ok else '不過'}")

    bp_ref = None
    for path in ("/dev-server/research/bp_s25_result.json",
                 "/tmp/fb/db/data/research/s25/bp_result.json"):
        if os.path.exists(path):
            j = json.load(open(path))
            bp_ref = {"baseline": {k: j["baseline"].get(k) for k in
                                   ("rps", "ece", "cs_cell_logloss", "ou25_logloss",
                                    "diag_mass_mean", "mode_11_share")},
                      "trials": [{"label": t["label"], "rps": t["rps"], "ece": t["ece"],
                                  "cs_cell_logloss": t["cs_cell_logloss"],
                                  "ou25_logloss": t["ou25_logloss"],
                                  "mode_draw_share": t.get("mode_draw_share"),
                                  "gate_pass": t.get("gate_pass")}
                                 for t in j.get("trials", [])]}
            break
    out = {"note": "S25 試驗 #4 DIBP on BP，研究軌；λ 層鎖死，未升指紋",
           "baseline": b, "trials": trials, "bp_reference": bp_ref}
    json.dump(out, open("/tmp/fb/s25_dibp_on_bp.json", "w"), ensure_ascii=False, indent=1)
    print("寫入 /tmp/fb/s25_dibp_on_bp.json")
