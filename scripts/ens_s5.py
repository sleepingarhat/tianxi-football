"""S5 集成＋校準 — Elo / Dixon-Coles / LGB 三軌 α 集成 + 向量標度／保序校準。賠率零權重 (market_beta=0)。
特徵建構與 S4 完全相同（同一份賽前特徵），集成權重與校準器只用「測試季之前」嘅賽季擬合。"""
import json, math, warnings
from collections import defaultdict, deque
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression

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


# ================= S5 ：三軌 α 集成 + 校準 =================
EPS = 1e-9
def norm(P):
    P = np.clip(P, EPS, None)
    return P / P.sum(1, keepdims=True)

seasons = sorted(set(season))
test_seasons = [s for s in seasons if s >= 2012]

# --- 軌 1：Elo（多項邏輯把分差映射成主客和，只用測試季之前資料擬合） ---
elo_cols = ["elo_diff", "elo_exp"]
# --- 軌 2：Dixon-Coles 比分矩陣派生 ---
dc_cols = ["dc_ph", "dc_pd", "dc_pa"]

P_elo = np.full((len(X), 3), np.nan)
P_dc = norm(X[dc_cols].values.astype(float))
P_lgb = np.full((len(X), 3), np.nan)

for s in test_seasons:
    tr = (season < s) & warm
    te = (season == s) & warm
    if tr.sum() < 20000 or te.sum() == 0:
        continue
    lr = LogisticRegression(max_iter=1000, C=1.0)
    lr.fit(X.loc[tr, elo_cols].values, y[tr])
    P_elo[te] = lr.predict_proba(X.loc[te, elo_cols].values)
    m = lgb.train(PARAMS, lgb.Dataset(X[tr], y[tr]), num_boost_round=450)
    P_lgb[te] = m.predict(X[te])
    print("track preds", s, int(te.sum()), flush=True)

have = ~np.isnan(P_lgb[:, 0])

def rps_of(P, yy):
    return rps(P, yy)

def metrics(P, yy):
    P = norm(P)
    return dict(rps=round(rps(P, yy), 4),
                logloss=round(float(-np.mean(np.log(P[np.arange(len(yy)), yy]))), 4),
                acc=round(float(np.mean(P.argmax(1) == yy)), 4),
                draw_recall=round(float(np.mean(P[yy == 1].argmax(1) == 1)), 4))

def blend(a, rows):
    """幾何（對數空間）加權集成：a = (w_lgb, w_dc, w_elo)"""
    L = (a[0] * np.log(np.clip(P_lgb[rows], EPS, None))
         + a[1] * np.log(np.clip(P_dc[rows], EPS, None))
         + a[2] * np.log(np.clip(P_elo[rows], EPS, None)))
    return norm(np.exp(L - L.max(1, keepdims=True)))

GRID = [(l / 20, d / 20, 1 - l / 20 - d / 20)
        for l in range(8, 21) for d in range(0, 13)
        if 1 - l / 20 - d / 20 >= -1e-9 and 1 - l / 20 - d / 20 <= 0.5]

class VectorScaler:
    """向量標度：對集成 log 機率再做一次多項邏輯（3 參數×3 類），專治和局壓縮。"""
    def fit(self, P, yy):
        self.lr = LogisticRegression(max_iter=2000, C=10.0)
        self.lr.fit(np.log(np.clip(P, EPS, None)), yy)
        return self
    def apply(self, P):
        return self.lr.predict_proba(np.log(np.clip(P, EPS, None)))

class IsoPerClass:
    """逐類保序回歸 + 重新歸一化。"""
    def fit(self, P, yy):
        self.models = []
        for k in range(3):
            iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-4, y_max=1 - 1e-4)
            iso.fit(P[:, k], (yy == k).astype(float))
            self.models.append(iso)
        return self
    def apply(self, P):
        Q = np.column_stack([self.models[k].predict(P[:, k]) for k in range(3)])
        return norm(Q)

DEFAULT_A = (0.70, 0.20, 0.10)
P_ens = np.full((len(X), 3), np.nan)
P_cal = np.full((len(X), 3), np.nan)
per_season, choices = [], []

for s in test_seasons:
    te = (season == s) & warm & have
    if te.sum() == 0:
        continue
    prev = [t for t in test_seasons if t < s]
    cal = (np.isin(season, prev[-2:])) & warm & have if prev else None
    if cal is not None and cal.sum() > 5000:
        # 1) 集成權重：喺前兩季嘅「季外」預測上挑 RPS 最低
        best = min(GRID, key=lambda a: rps(blend(a, cal), y[cal]))
        # 2) 校準器：同一批前季資料擬合，兩種校準各自評分後揀贏嘅（唔睇測試季）
        Pc = blend(best, cal)
        cands = {"none": None, "vector": VectorScaler().fit(Pc, y[cal]), "isotonic": IsoPerClass().fit(Pc, y[cal])}
        half = cal.copy()
        idx = np.where(cal)[0]
        hold = np.zeros(len(X), bool); hold[idx[len(idx) // 2:]] = True   # 後半季作校準器挑選
        fitm = np.zeros(len(X), bool); fitm[idx[:len(idx) // 2]] = True
        Pf = blend(best, fitm)
        picks = {"none": None,
                 "vector": VectorScaler().fit(Pf, y[fitm]),
                 "isotonic": IsoPerClass().fit(Pf, y[fitm])}
        Ph = blend(best, hold)
        scores = {k: rps(Ph if v is None else v.apply(Ph), y[hold]) for k, v in picks.items()}
        kind = min(scores, key=scores.get)
        cal_model = cands[kind]
    else:
        best, kind, cal_model = DEFAULT_A, "none", None
    Pe = blend(best, te)
    P_ens[te] = Pe
    P_cal[te] = Pe if cal_model is None else cal_model.apply(Pe)
    yy = y[te]
    row = dict(season=int(s), n=int(te.sum()), alpha=[round(v, 2) for v in best], calib=kind,
               **metrics(P_cal[te], yy))
    row["lgb_rps"] = round(rps(norm(P_lgb[te]), yy), 4)
    per_season.append(row)
    choices.append(dict(season=int(s), alpha=[round(v, 2) for v in best], calib=kind))
    print(row, flush=True)

ok = have & warm & ~np.isnan(P_cal[:, 0])
yy = y[ok]
res = dict(
    n=int(ok.sum()), span=f"{min(test_seasons)}-{max(test_seasons)}",
    market_beta=0,
    ensemble_calibrated=metrics(P_cal[ok], yy),
    ensemble_raw=metrics(P_ens[ok], yy),
    tracks=dict(lgb_s4=metrics(P_lgb[ok], yy), dc_s3=metrics(P_dc[ok], yy), elo_s2=metrics(P_elo[ok], yy)),
    per_season=per_season, choices=choices,
)
res["market_devig_reference"] = dict(rps=0.2047, logloss=1.0056, acc=0.5010, note="僅對照，永不入模")

# 校準表（最大機率 10 區）
for tag, P in (("calibrated", P_cal[ok]), ("raw", P_ens[ok])):
    P = norm(P)
    mp = P.max(1); hit = (P.argmax(1) == yy).astype(float)
    b = np.clip((mp * 10).astype(int), 0, 9)
    tbl = [dict(bin=f"{i/10:.1f}-{(i+1)/10:.1f}", n=int((b == i).sum()),
                pred=round(float(mp[b == i].mean()), 4), act=round(float(hit[b == i].mean()), 4))
           for i in range(10) if (b == i).sum() > 50]
    res[f"calibration_{tag}"] = tbl
    res[f"ece_{tag}"] = round(float(sum(abs(c["pred"] - c["act"]) * c["n"] for c in tbl) / ok.sum()), 4)

# 和局分項：逐區可靠度
pd_ = norm(P_cal[ok])[:, 1]
b = np.clip((pd_ * 20).astype(int), 0, 19)
res["draw_reliability"] = [dict(bin=f"{i/20:.2f}-{(i+1)/20:.2f}", n=int((b == i).sum()),
                                pred=round(float(pd_[b == i].mean()), 4),
                                act=round(float((yy[b == i] == 1).mean()), 4))
                           for i in range(20) if (b == i).sum() > 200]

json.dump(res, open("/tmp/fb/ens_s5.json", "w"), indent=2, ensure_ascii=False)
print(json.dumps({k: v for k, v in res.items() if not isinstance(v, list)}, indent=2, ensure_ascii=False))
print(json.dumps(res["draw_reliability"], indent=1))
