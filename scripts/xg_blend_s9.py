"""S9 xG 混合目標值驗證（供應商中立版）

輸入：CSV，欄位 lg,date,home,away,gh,ga,xh,xa（xh/xa = 賽前不可知嘅賽後 xG，只用於訓練目標值）
輸出：逐個混合權重 w_goals 嘅 walk-forward 成績（RPS / log-loss / 主客和命中 / 波膽命中）

紀律：
- 只用開賽前已完成場次擬合，每 14 日滾動重訓，時間半衰期 180 日
- 賠率零權重，永不入模
- 禁爬來源一律不得作為輸入；請接已授權供應（FootyStats API / TheSports / Opta）
"""
import csv, datetime, sys
from collections import defaultdict
from math import exp, factorial, log

HALF = 180.0
MAXG = 8
RHO = -0.05
START = datetime.date(2020, 8, 1)


def load(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append(dict(lg=r["lg"], d=datetime.date.fromisoformat(r["date"]),
                             h=r["home"], a=r["away"], gh=int(r["gh"]), ga=int(r["ga"]),
                             xh=float(r["xh"]), xa=float(r["xa"])))
    rows.sort(key=lambda r: r["d"])
    return rows


def fit(hist, now, w, iters=60):
    teams = {t for r in hist for t in (r["h"], r["a"])}
    if not teams:
        return None
    atk = {t: 1.0 for t in teams}
    dfn = {t: 1.0 for t in teams}
    ha = 1.3
    data = []
    for r in hist:
        wt = 0.5 ** ((now - r["d"]).days / HALF)
        if wt < 0.02:
            continue
        data.append((r["h"], r["a"], w * r["gh"] + (1 - w) * r["xh"],
                     w * r["ga"] + (1 - w) * r["xa"], wt))
    if not data:
        return None
    for _ in range(iters):
        num, den = defaultdict(float), defaultdict(float)
        for h, a, eh, ea, wt in data:
            num[h] += wt * eh; den[h] += wt * dfn[a] * ha
            num[a] += wt * ea; den[a] += wt * dfn[h]
        for t in teams:
            if den[t] > 0:
                atk[t] = max(0.05, min(8.0, num[t] / den[t]))
        g = exp(sum(log(atk[t]) for t in teams) / len(teams))
        for t in teams:
            atk[t] /= g
        nd, dd = defaultdict(float), defaultdict(float)
        for h, a, eh, ea, wt in data:
            nd[a] += wt * eh; dd[a] += wt * atk[h] * ha
            nd[h] += wt * ea; dd[h] += wt * atk[a]
        for t in teams:
            if dd[t] > 0:
                dfn[t] = max(0.05, min(8.0, nd[t] / dd[t]))
        hn = hd = 0.0
        for h, a, eh, ea, wt in data:
            hn += wt * eh; hd += wt * atk[h] * dfn[a]
        if hd > 0:
            ha = max(0.8, min(2.0, hn / hd))
    return atk, dfn, ha


def pois(l, k):
    return exp(-l) * l ** k / factorial(k)


def matrix(lh, la):
    P = [[pois(lh, i) * pois(la, j) for j in range(MAXG + 1)] for i in range(MAXG + 1)]
    P[0][0] *= 1 - lh * la * RHO; P[0][1] *= 1 + lh * RHO
    P[1][0] *= 1 + la * RHO; P[1][1] *= 1 - RHO
    s = sum(map(sum, P))
    return [[v / s for v in row] for row in P]


def run(M, w):
    rps = ll = 0.0
    n = acc = cs = 0
    model, last = {}, None
    leagues = {r["lg"] for r in M}
    for i, r in enumerate(M):
        if r["d"] < START:
            continue
        if last is None or (r["d"] - last).days >= 14:
            last = r["d"]
            model = {lg: fit([x for x in M[:i] if x["lg"] == lg], r["d"], w) for lg in leagues}
        mo = model.get(r["lg"])
        if not mo:
            continue
        atk, dfn, ha = mo
        if r["h"] not in atk or r["a"] not in atk:
            continue
        lh = min(5, max(0.15, atk[r["h"]] * dfn[r["a"]] * ha))
        la = min(5, max(0.15, atk[r["a"]] * dfn[r["h"]]))
        P = matrix(lh, la)
        ph = sum(P[i2][j] for i2 in range(MAXG + 1) for j in range(MAXG + 1) if i2 > j)
        pdw = sum(P[k][k] for k in range(MAXG + 1))
        p = [ph, pdw, 1 - ph - pdw]
        res = 0 if r["gh"] > r["ga"] else (1 if r["gh"] == r["ga"] else 2)
        c = co = 0.0
        for k in range(2):
            c += p[k]; co += 1 if k == res else 0
            rps += (c - co) ** 2
        ll += -log(max(1e-9, p[res]))
        if max(range(3), key=lambda k: p[k]) == res:
            acc += 1
        bi, bj = max(((a, b) for a in range(MAXG + 1) for b in range(MAXG + 1)), key=lambda t: P[t[0]][t[1]])
        if (bi, bj) == (r["gh"], r["ga"]):
            cs += 1
        n += 1
    return dict(n=n, rps=rps / (2 * n), logloss=ll / n, acc=acc / n, score_hit=cs / n)


if __name__ == "__main__":
    M = load(sys.argv[1])
    for w in (1.0, 0.5, 0.4, 0.35, 0.3, 0.2, 0.0):
        print(w, run(M, w), flush=True)
