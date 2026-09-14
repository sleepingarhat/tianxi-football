#!/usr/bin/env python3
"""S25 試驗 #3：邊際換 CMP／負二項（只改邊際形狀，唔加相關）

凍結：λ_H / λ_A 生成式完全等同現行生產軌（全局 atk/dfn、gamma=0.12、lr=0.04、
regress=0.80、warm=40、逐聯賽 base、DC rho=-0.05）。主客分拆已於 S23 否決並鎖死。

做法：把每邊嘅泊松邊際換成
  * 負二項（overdispersed，肥尾）：p = r/(r+λ)，均值嚴格對齊 λ
  * Conway–Maxwell–Poisson（ν>1 收窄／ν<1 肥尾）：解 θ 令均值 = λ
再照舊做獨立外積 + 現行 DC 低分修正，即係只改邊際形狀，均值同期望入球不變。

閘門：主閘 RPS、實際比分格 log-loss、ECE（1X2 十桶）；
副閘 大細 2.5 log-loss、對角總質量對經驗和局率、頭八格覆蓋；逐季唔准輸。
成功樣：肥尾／多零改善、1-1 眾數自然跌；失敗樣：高分格過肥。
"""
import csv, json, math, os
from collections import defaultdict

CSV = "/tmp/fb/Matches.csv"
MAXG = 10
EVAL_FROM = 2021

_FACT = [math.factorial(k) for k in range(MAXG + 2)]


def season_of(y, m):
    return y if m >= 7 else y - 1


def pois_pmf(lam):
    p = [math.exp(-lam)]
    for k in range(1, MAXG + 1):
        p.append(p[-1] * lam / k)
    return p


def nb_pmf(lam, r):
    """負二項，均值 = lam，方差 = lam(1+lam/r)。r→∞ 回歸泊松。"""
    p = r / (r + lam)
    q = 1 - p
    out = [p ** r]
    for k in range(1, MAXG + 1):
        out.append(out[-1] * q * (r + k - 1) / k)
    return out


def cmp_pmf(lam, nu):
    """CMP：P(k) ∝ θ^k /(k!)^nu，用二分法解 θ 令均值 = lam。"""
    def mean_of(theta):
        w = [theta ** k / (_FACT[k] ** nu) for k in range(MAXG + 1)]
        z = sum(w)
        return sum(k * w[k] for k in range(MAXG + 1)) / z, [v / z for v in w]

    lo, hi = 1e-6, 50.0
    for _ in range(80):
        mid = (lo + hi) / 2
        m, _w = mean_of(mid)
        if m < lam:
            lo = mid
        else:
            hi = mid
    _m, w = mean_of((lo + hi) / 2)
    return w


def marginal(lam, kind, param):
    if kind == "pois":
        return pois_pmf(lam)
    if kind == "nb":
        return nb_pmf(lam, param)
    return cmp_pmf(lam, param)


def dc_tweak(M, lh, la, rho):
    if rho == 0:
        return M
    for (i, j), t in (((0, 0), 1 - lh * la * rho), ((0, 1), 1 + lh * rho),
                      ((1, 0), 1 + la * rho), ((1, 1), 1 - rho)):
        M[i][j] *= max(t, 1e-9)
    return M


def build_matrix(lh, la, rho, kind="pois", param=0.0):
    ph = marginal(lh, kind, param)
    pa = marginal(la, kind, param)
    M = [[ph[i] * pa[j] for j in range(MAXG + 1)] for i in range(MAXG + 1)]
    M = dc_tweak(M, lh, la, rho)
    s = sum(sum(row) for row in M)
    return [[v / s for v in row] for row in M]


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


def run(rows, label, *, kind="pois", param=0.0, rho=-0.05,
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
    zero_mass = high_mass = 0.0
    bins = defaultdict(lambda: [0, 0.0, 0])
    mode_mix = defaultdict(int)
    per_season = defaultdict(lambda: [0, 0.0])
    per_div = defaultdict(lambda: [0, 0.0, 0.0, 0])

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

        M = build_matrix(lh, la, rho, kind, param)
        pH, pD, pA, pOver, cells = derive(M)

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
            zero_mass += M[0][0]
            high_mass += sum(M[i][j] for i in range(MAXG + 1) for j in range(MAXG + 1)
                             if i + j >= 6)
            draws += int(r["res"] == "D")
            yo = 1 if r["gh"] + r["ga"] > 2.5 else 0
            ou_ll += -math.log(max(pOver if yo else 1 - pOver, 1e-12))
            per_season[sea][0] += 1
            per_season[sea][1] += rps3(p, k)
            pv = per_div[d]
            pv[0] += 1
            pv[1] += rps3(p, k)
            pv[2] += pD
            pv[3] += int(r["res"] == "D")
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
        "marginal": kind,
        "param": param,
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
        "zero_zero_mass": round(zero_mass / n, 4),
        "high_score_mass": round(high_mass / n, 4),
        "mode_11_share": round(mode_mix.get("1-1", 0) / n, 4),
        "mode_draw_share": round(
            sum(c for s, c in mode_mix.items() if s.split("-")[0] == s.split("-")[1]) / n, 4),
        "per_season_rps": {str(s): round(v[1] / v[0], 5) for s, v in sorted(per_season.items())},
        "per_div": {k: {"n": v[0], "rps": round(v[1] / v[0], 5),
                        "diag": round(v[2] / v[0], 4),
                        "draw_actual": round(v[3] / v[0], 4)}
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
    b = run(rows, "生產基準（泊松邊際 + DC rho=-0.05）")
    print(f"基準 RPS {b['rps']} 格LL {b['cs_cell_logloss']} ECE {b['ece']} "
          f"OU LL {b['ou25_logloss']} 對角 {b['diag_mass_mean']} 實際和 {b['draw_freq_actual']} "
          f"1-1眾數 {b['mode_11_share']} 0-0 {b['zero_zero_mass']} 高分格 {b['high_score_mass']}")

    trials = []
    for r_ in (2.0, 4.0, 8.0, 16.0, 32.0, 64.0):
        trials.append(run(rows, f"負二項 r={r_:g} + DC", kind="nb", param=r_, rho=-0.05))
    for nu in (0.85, 0.92, 0.97, 1.03, 1.10, 1.25):
        trials.append(run(rows, f"CMP ν={nu:g} + DC", kind="cmp", param=nu, rho=-0.05))
    # 純邊際（rho=0）：驗肥尾／收窄可唔可以取代 DC 低分修正
    trials.append(run(rows, "負二項 r=8（rho=0）", kind="nb", param=8.0, rho=0.0))
    trials.append(run(rows, "CMP ν=1.10（rho=0）", kind="cmp", param=1.10, rho=0.0))

    for c in trials:
        main, sub, sea_ok, ok = gates(c, b)
        c["gate_main"], c["gate_sub"], c["gate_season"], c["gate_pass"] = main, sub, sea_ok, ok
        print(f"{c['label']:24s} RPS {c['rps']:.5f} 格LL {c['cs_cell_logloss']:.4f} "
              f"ECE {c['ece']:.5f} OU {c['ou25_logloss']:.5f} 對角 {c['diag_mass_mean']:.4f} "
              f"0-0 {c['zero_zero_mass']:.4f} 高分 {c['high_score_mass']:.4f} "
              f"1-1眾數 {c['mode_11_share']:.3f} → 主閘 {sum(main.values())}/3 "
              f"副閘 {sum(sub.values())}/3 逐季 {'過' if sea_ok else '不過'} "
              f"→ {'過' if ok else '不過'}")

    ref = {}
    for name, paths in (("bp", ("/dev-server/research/bp_s25_result.json",
                                "/tmp/fb/db/data/research/s25/bp_result.json")),
                        ("dibp", ("/dev-server/research/dibp_s25_result.json",
                                  "/tmp/fb/db/data/research/s25/dibp_result.json"))):
        for path in paths:
            if os.path.exists(path):
                j = json.load(open(path))
                ref[name] = {
                    "baseline": {k: j["baseline"].get(k) for k in
                                 ("rps", "ece", "cs_cell_logloss", "ou25_logloss",
                                  "diag_mass_mean", "mode_11_share")},
                    "trials": [{"label": t["label"], "rps": t["rps"], "ece": t["ece"],
                                "cs_cell_logloss": t["cs_cell_logloss"],
                                "ou25_logloss": t["ou25_logloss"],
                                "mode_11_share": t.get("mode_11_share"),
                                "gate_pass": t.get("gate_pass")}
                               for t in j.get("trials", [])]}
                break
    out = {"note": "S25 試驗 #3 邊際換 CMP／負二項，研究軌；λ 層鎖死，未升指紋",
           "baseline": b, "trials": trials, "reference": ref}
    json.dump(out, open("/tmp/fb/s25_cmp.json", "w"), ensure_ascii=False, indent=1)
    print("寫入 /tmp/fb/s25_cmp.json")
