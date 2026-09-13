"""S4 天喜足球LGB — walk-forward, 賠率零權重 (market_beta=0)."""
import json, math, warnings
from collections import defaultdict, deque
import numpy as np, pandas as pd, lightgbm as lgb

warnings.filterwarnings("ignore")
SRC = "/tmp/fb/Matches.csv"

df = pd.read_csv(SRC, low_memory=False)
df["MatchDate"] = pd.to_datetime(df["MatchDate"], errors="coerce")
df = df.dropna(subset=["MatchDate", "HomeTeam", "AwayTeam", "FTHome", "FTAway", "FTResult"])
df = df[df["FTResult"].isin(["H", "D", "A"])].sort_values(["MatchDate", "Division", "HomeTeam"]).reset_index(drop=True)
df["season"] = np.where(df["MatchDate"].dt.month >= 7, df["MatchDate"].dt.year, df["MatchDate"].dt.year - 1)
print("matches", len(df), df["MatchDate"].min().date(), df["MatchDate"].max().date())

# ---------- online state ----------
ELO_HOME_ADV, K, REG = 60.0, 22.0, 0.70
elo = defaultdict(lambda: 1500.0)
# Dixon-Coles online
ATK, DEF = defaultdict(lambda: 0.0), defaultdict(lambda: 0.0)
GAMMA, RHO, LR = 0.12, -0.05, 0.010
lg_mu = defaultdict(lambda: 0.30)  # log avg goals per team per league
seen = defaultdict(int)
last_season = {}
hist = defaultdict(lambda: deque(maxlen=10))       # per team: dicts
hist_home = defaultdict(lambda: deque(maxlen=5))
hist_away = defaultdict(lambda: deque(maxlen=5))
last_date, dates14 = {}, defaultdict(lambda: deque(maxlen=12))
h2h = defaultdict(lambda: deque(maxlen=6))

MAXG = 8
def score_matrix(lh, la):
    ph = np.exp(-lh) * lh ** np.arange(MAXG) / np.array([math.factorial(i) for i in range(MAXG)])
    pa = np.exp(-la) * la ** np.arange(MAXG) / np.array([math.factorial(i) for i in range(MAXG)])
    M = np.outer(ph, pa)
    t = 1.0
    M[0, 0] *= 1 - lh * la * RHO; M[0, 1] *= 1 + lh * RHO
    M[1, 0] *= 1 + la * RHO;      M[1, 1] *= 1 - RHO
    M = np.clip(M, 1e-12, None); M /= M.sum()
    return M

def agg(dq, key):
    if not dq: return np.nan
    return float(np.mean([d[key] for d in dq if d[key] == d[key]])) if any(d[key] == d[key] for d in dq) else np.nan

rows, ys = [], []
for r in df.itertuples(index=False):
    h, a, div = r.HomeTeam, r.AwayTeam, r.Division
    # season regression
    for t in (h, a):
        if last_season.get(t) not in (None, r.season):
            elo[t] = 1500 + (elo[t] - 1500) * REG
            ATK[t] *= 0.80; DEF[t] *= 0.80
        last_season[t] = r.season
    eh, ea = elo[h], elo[a]
    lh = math.exp(lg_mu[div] + ATK[h] - DEF[a] + GAMMA)
    la = math.exp(lg_mu[div] + ATK[a] - DEF[h])
    lh, la = min(max(lh, 0.15), 6.0), min(max(la, 0.15), 6.0)
    M = score_matrix(lh, la)
    p_h = float(np.tril(M, -1).sum()); p_d = float(np.trace(M)); p_a = float(np.triu(M, 1).sum())
    idx = np.add.outer(np.arange(MAXG), np.arange(MAXG))
    p_o25 = float(M[idx >= 3].sum())
    p_btts = float(M[1:, 1:].sum())
    dr = (r.MatchDate - last_date[h]).days if h in last_date else np.nan
    dra = (r.MatchDate - last_date[a]).days if a in last_date else np.nan
    n14h = sum(1 for d in dates14[h] if (r.MatchDate - d).days <= 14)
    n14a = sum(1 for d in dates14[a] if (r.MatchDate - d).days <= 14)
    hh = h2h[(h, a) if h < a else (a, h)]
    feat = dict(
        elo_home=eh, elo_away=ea, elo_diff=eh - ea,
        elo_exp=1 / (1 + 10 ** (-(eh + ELO_HOME_ADV - ea) / 400)),
        lam_h=lh, lam_a=la, lam_sum=lh + la, lam_diff=lh - la,
        dc_ph=p_h, dc_pd=p_d, dc_pa=p_a, dc_o25=p_o25, dc_btts=p_btts,
        atk_h=ATK[h], def_h=DEF[h], atk_a=ATK[a], def_a=DEF[a],
        seen_h=seen[h], seen_a=seen[a],
        form3h=r.Form3Home, form5h=r.Form5Home, form3a=r.Form3Away, form5a=r.Form5Away,
        rest_h=dr, rest_a=dra, rest_diff=(dr - dra) if dr == dr and dra == dra else np.nan,
        n14_h=n14h, n14_a=n14a,
        h2h_n=len(hh), h2h_gd=agg(hh, "gd_h") if hh else np.nan,
        month=r.MatchDate.month, dow=r.MatchDate.dayofweek,
    )
    for tag, dq in (("h", hist[h]), ("a", hist[a])):
        for k in ("gf", "ga", "pts", "shots", "target", "corners", "cards"):
            feat[f"{k}_l10_{tag}"] = agg(dq, k)
    for k in ("gf", "ga", "pts"):
        feat[f"{k}_venue_h"] = agg(hist_home[h], k)
        feat[f"{k}_venue_a"] = agg(hist_away[a], k)
    feat["gfd_l10"] = (feat["gf_l10_h"] or np.nan) - (feat["gf_l10_a"] or np.nan)
    rows.append(feat)
    ys.append((r.FTResult, float(r.FTHome), float(r.FTAway)))

    # ---- update states with the actual result ----
    gh, ga_ = float(r.FTHome), float(r.FTAway)
    sc = 1.0 if gh > ga_ else (0.5 if gh == ga_ else 0.0)
    exp = 1 / (1 + 10 ** (-(eh + ELO_HOME_ADV - ea) / 400))
    kk = K * (1 + min(abs(gh - ga_), 4) * 0.25)
    elo[h] = eh + kk * (sc - exp); elo[a] = ea - kk * (sc - exp)
    ATK[h] += LR * (gh - lh); DEF[a] -= LR * (gh - lh)
    ATK[a] += LR * (ga_ - la); DEF[h] -= LR * (ga_ - la)
    lg_mu[div] += 0.0005 * ((gh + ga_) - (lh + la))
    seen[h] += 1; seen[a] += 1
    def rec(gf, gag, sh, tg, co, ca):
        return dict(gf=gf, ga=gag, pts=3.0 if gf > gag else (1.0 if gf == gag else 0.0),
                    shots=sh, target=tg, corners=co, cards=ca, gd_h=gf - gag)
    rh = rec(gh, ga_, r.HomeShots, r.HomeTarget, r.HomeCorners,
             (r.HomeYellow if r.HomeYellow == r.HomeYellow else np.nan))
    ra = rec(ga_, gh, r.AwayShots, r.AwayTarget, r.AwayCorners,
             (r.AwayYellow if r.AwayYellow == r.AwayYellow else np.nan))
    hist[h].append(rh); hist[a].append(ra); hist_home[h].append(rh); hist_away[a].append(ra)
    last_date[h] = last_date[a] = r.MatchDate
    dates14[h].append(r.MatchDate); dates14[a].append(r.MatchDate)
    hh.append(rh if h < a else ra)

X = pd.DataFrame(rows)
X["div"] = df["Division"].astype("category")
lab = {"H": 0, "D": 1, "A": 2}
y = np.array([lab[t[0]] for t in ys])
btts = np.array([1 if (t[1] > 0 and t[2] > 0) else 0 for t in ys])
o25 = np.array([1 if (t[1] + t[2]) > 2.5 else 0 for t in ys])
season = df["season"].values
warm = X["seen_h"].values >= 40
print("features", X.shape[1])

def rps(P, yy):
    Y = np.zeros_like(P); Y[np.arange(len(yy)), yy] = 1
    return float(np.mean(np.sum((np.cumsum(P[:, :2], 1) - np.cumsum(Y[:, :2], 1)) ** 2, 1) / 2))

PARAMS = dict(objective="multiclass", num_class=3, learning_rate=0.04, num_leaves=48,
              min_data_in_leaf=200, feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
              lambda_l2=2.0, verbose=-1, num_threads=4)
BPARAMS = dict(PARAMS); BPARAMS.update(objective="binary"); BPARAMS.pop("num_class")

seasons = sorted(set(season))
test_seasons = [s for s in seasons if s >= 2012]
P = np.full((len(X), 3), np.nan); Pb = np.full(len(X), np.nan); Po = np.full(len(X), np.nan)
per_season = []
for s in test_seasons:
    tr = (season < s) & warm
    te = (season == s) & warm
    if tr.sum() < 20000 or te.sum() == 0: continue
    m = lgb.train(PARAMS, lgb.Dataset(X[tr], y[tr]), num_boost_round=450)
    P[te] = m.predict(X[te])
    mb = lgb.train(BPARAMS, lgb.Dataset(X[tr], btts[tr]), num_boost_round=350)
    Pb[te] = mb.predict(X[te])
    mo = lgb.train(BPARAMS, lgb.Dataset(X[tr], o25[tr]), num_boost_round=350)
    Po[te] = mo.predict(X[te])
    pr, yy = P[te], y[te]
    per_season.append(dict(season=int(s), n=int(te.sum()), rps=round(rps(pr, yy), 4),
                           logloss=round(float(-np.mean(np.log(pr[np.arange(len(yy)), yy]))), 4),
                           acc=round(float(np.mean(pr.argmax(1) == yy)), 4),
                           draw_recall=round(float(np.mean(pr[yy == 1].argmax(1) == 1)) if (yy == 1).sum() else 0, 4)))
    print(per_season[-1], flush=True)

ok = ~np.isnan(P[:, 0])
yy, pr = y[ok], P[ok]
res = dict(
    n=int(ok.sum()), span=f"{min(test_seasons)}-{max(test_seasons)}",
    rps=round(rps(pr, yy), 4),
    logloss=round(float(-np.mean(np.log(pr[np.arange(len(yy)), yy]))), 4),
    acc=round(float(np.mean(pr.argmax(1) == yy)), 4),
    draw_recall=round(float(np.mean(pr[yy == 1].argmax(1) == 1)), 4),
    btts_logloss=round(float(-np.mean(btts[ok] * np.log(Pb[ok]) + (1 - btts[ok]) * np.log(1 - Pb[ok]))), 4),
    o25_logloss=round(float(-np.mean(o25[ok] * np.log(Po[ok]) + (1 - o25[ok]) * np.log(1 - Po[ok]))), 4),
)
# DC baseline on same rows (S3 comparison)
d = X.loc[ok, ["dc_ph", "dc_pd", "dc_pa"]].values
d = d / d.sum(1, keepdims=True)
res["dc_same_rows"] = dict(rps=round(rps(d, yy), 4),
                           logloss=round(float(-np.mean(np.log(d[np.arange(len(yy)), yy]))), 4),
                           acc=round(float(np.mean(d.argmax(1) == yy)), 4),
                           draw_recall=round(float(np.mean(d[yy == 1].argmax(1) == 1)), 4))
pb_dc = X.loc[ok, "dc_btts"].values.clip(1e-6, 1 - 1e-6)
po_dc = X.loc[ok, "dc_o25"].values.clip(1e-6, 1 - 1e-6)
res["dc_same_rows"]["btts_logloss"] = round(float(-np.mean(btts[ok] * np.log(pb_dc) + (1 - btts[ok]) * np.log(1 - pb_dc))), 4)
res["dc_same_rows"]["o25_logloss"] = round(float(-np.mean(o25[ok] * np.log(po_dc) + (1 - o25[ok]) * np.log(1 - po_dc))), 4)
br = btts[ok].mean(); orr = o25[ok].mean()
res["base_rate"] = dict(btts=round(float(-(br * math.log(br) + (1 - br) * math.log(1 - br))), 4),
                        o25=round(float(-(orr * math.log(orr) + (1 - orr) * math.log(1 - orr))), 4))
# calibration (10 bins, max prob)
mp = pr.max(1); hit = (pr.argmax(1) == yy).astype(float)
bins = np.clip((mp * 10).astype(int), 0, 9)
res["calibration"] = [dict(bin=f"{b/10:.1f}-{(b+1)/10:.1f}", n=int((bins == b).sum()),
                           pred=round(float(mp[bins == b].mean()), 4), act=round(float(hit[bins == b].mean()), 4))
                      for b in range(10) if (bins == b).sum() > 50]
res["ece"] = round(float(sum(abs(c["pred"] - c["act"]) * c["n"] for c in res["calibration"]) / ok.sum()), 4)
res["per_season"] = per_season
imp = sorted(zip(X.columns, m.feature_importance("gain")), key=lambda t: -t[1])[:20]
res["top_gain"] = [dict(f=f, gain=round(float(g), 1)) for f, g in imp]
json.dump(res, open("/tmp/fb/lgb_s4.json", "w"), indent=2, ensure_ascii=False)
print(json.dumps({k: v for k, v in res.items() if k not in ("per_season", "calibration", "top_gain")}, indent=2))
print(json.dumps(res["top_gain"][:12], indent=1))
print(json.dumps(res["calibration"], indent=1))
