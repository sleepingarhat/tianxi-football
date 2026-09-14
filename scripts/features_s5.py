#!/usr/bin/env python3
"""S5 共用賽前特徵引擎（訓練同凍結推論用同一份代碼，避免特徵分叉）。

鐵律：
  · 所有特徵只用「開賽前已存在」嘅資料（歷史賽果滾動），賽後統計只喺 update() 事後入狀態。
  · 賠率零權重（market_beta = 0），本模組完全唔讀任何賠率欄。
  · 訓練（train_s5.py）同凍結推論（predict_fixtures.py）必須 import 同一個 FeatureEngine，
    唔准各自複製一份特徵碼——分叉就係洩漏溫床。
"""
import csv, glob, math, os
from collections import defaultdict, deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "data", "results")

# 在線狀態參數（同 S4／S5 回測一致）
ELO_HOME_ADV, K, REG = 60.0, 22.0, 0.70
GAMMA, RHO, LR, MU0, SREG = 0.12, -0.05, 0.010, 0.30, 0.80
MAXG = 8
WARM = 40

_FACT = [math.factorial(i) for i in range(MAXG)]

BASE_COLS = [
    "elo_home", "elo_away", "elo_diff", "elo_exp",
    "lam_h", "lam_a", "lam_sum", "lam_diff",
    "dc_ph", "dc_pd", "dc_pa", "dc_o25", "dc_btts",
    "atk_h", "def_h", "atk_a", "def_a",
    "seen_h", "seen_a",
    "form3h", "form5h", "form3a", "form5a",
    "rest_h", "rest_a", "rest_diff", "n14_h", "n14_a",
    "h2h_n", "h2h_gd", "month", "dow", "gfd_l10", "div_id",
]
ROLL_KEYS = ("gf", "ga", "pts", "shots", "target", "corners", "cards")
FEATURE_COLS = (
    BASE_COLS
    + [f"{k}_l10_{t}" for t in ("h", "a") for k in ROLL_KEYS]
    + [f"{k}_venue_{t}" for t in ("h", "a") for k in ("gf", "ga", "pts")]
)

NAN = float("nan")


def score_matrix(lh, la, maxg=MAXG, rho=RHO):
    ph = [math.exp(-lh) * lh ** i / _FACT[i] for i in range(maxg)]
    pa = [math.exp(-la) * la ** j / _FACT[j] for j in range(maxg)]
    M = [[ph[i] * pa[j] for j in range(maxg)] for i in range(maxg)]
    M[0][0] *= 1 - lh * la * rho
    M[0][1] *= 1 + lh * rho
    M[1][0] *= 1 + la * rho
    M[1][1] *= 1 - rho
    for i in range(maxg):
        for j in range(maxg):
            M[i][j] = max(M[i][j], 1e-12)
    s = sum(sum(r) for r in M)
    return [[v / s for v in r] for r in M]


def _mean(vals):
    vs = [v for v in vals if v is not None and v == v]
    return float(sum(vs) / len(vs)) if vs else NAN


def _ord(iso):
    y, m, d = int(iso[:4]), int(iso[5:7]), int(iso[8:10])
    return (y * 12 + m) * 31 + d  # 粗略日序，只用於休息日／密度特徵


def season_of(iso):
    y, m = int(iso[:4]), int(iso[5:7])
    return y if m >= 7 else y - 1


class FeatureEngine:
    def __init__(self):
        self.elo = defaultdict(lambda: 1500.0)
        self.atk = defaultdict(float)
        self.dfn = defaultdict(float)
        self.mu = defaultdict(lambda: MU0)
        self.seen = defaultdict(int)
        self.last_season = {}
        self.hist = defaultdict(lambda: deque(maxlen=10))
        self.hist_home = defaultdict(lambda: deque(maxlen=5))
        self.hist_away = defaultdict(lambda: deque(maxlen=5))
        self.last_day = {}
        self.days = defaultdict(lambda: deque(maxlen=12))
        self.h2h = defaultdict(lambda: deque(maxlen=6))
        self.divs = set()

    # --- 賽季回歸：喺出特徵之前執行（同回測次序一致） ---
    def roll_season(self, iso, home, away):
        s = season_of(iso)
        for t in (home, away):
            if self.last_season.get(t) not in (None, s):
                self.elo[t] = 1500 + (self.elo[t] - 1500) * REG
                self.atk[t] *= SREG
                self.dfn[t] *= SREG
            self.last_season[t] = s

    def lambdas(self, div, home, away):
        lh = math.exp(self.mu[div] + self.atk[home] - self.dfn[away] + GAMMA)
        la = math.exp(self.mu[div] + self.atk[away] - self.dfn[home])
        return min(max(lh, 0.15), 6.0), min(max(la, 0.15), 6.0)

    def features(self, iso, div, home, away, div_ids=None):
        self.divs.add(div)
        eh, ea = self.elo[home], self.elo[away]
        lh, la = self.lambdas(div, home, away)
        M = score_matrix(lh, la)
        ph = pd_ = pa = o25 = btts = 0.0
        for i in range(MAXG):
            for j in range(MAXG):
                p = M[i][j]
                if i > j:
                    ph += p
                elif i == j:
                    pd_ += p
                else:
                    pa += p
                if i + j >= 3:
                    o25 += p
                if i > 0 and j > 0:
                    btts += p
        day = _ord(iso)
        rh = day - self.last_day[home] if home in self.last_day else NAN
        ra = day - self.last_day[away] if away in self.last_day else NAN
        n14h = sum(1 for d in self.days[home] if day - d <= 14)
        n14a = sum(1 for d in self.days[away] if day - d <= 14)
        hh = self.h2h[(home, away) if home < away else (away, home)]
        H, A = self.hist[home], self.hist[away]

        def pts_last(dq, n):
            xs = [d["pts"] for d in list(dq)[-n:]]
            return float(sum(xs)) if xs else NAN

        f = {
            "elo_home": eh, "elo_away": ea, "elo_diff": eh - ea,
            "elo_exp": 1 / (1 + 10 ** (-(eh + ELO_HOME_ADV - ea) / 400)),
            "lam_h": lh, "lam_a": la, "lam_sum": lh + la, "lam_diff": lh - la,
            "dc_ph": ph, "dc_pd": pd_, "dc_pa": pa, "dc_o25": o25, "dc_btts": btts,
            "atk_h": self.atk[home], "def_h": self.dfn[home],
            "atk_a": self.atk[away], "def_a": self.dfn[away],
            "seen_h": self.seen[home], "seen_a": self.seen[away],
            "form3h": pts_last(H, 3), "form5h": pts_last(H, 5),
            "form3a": pts_last(A, 3), "form5a": pts_last(A, 5),
            "rest_h": rh, "rest_a": ra,
            "rest_diff": (rh - ra) if (rh == rh and ra == ra) else NAN,
            "n14_h": n14h, "n14_a": n14a,
            "h2h_n": len(hh), "h2h_gd": _mean([d["gd_h"] for d in hh]) if hh else NAN,
            "month": int(iso[5:7]), "dow": (day % 7),
            "div_id": (div_ids or {}).get(div, -1),
        }
        for tag, dq in (("h", H), ("a", A)):
            for k in ROLL_KEYS:
                f[f"{k}_l10_{tag}"] = _mean([d[k] for d in dq])
        for k in ("gf", "ga", "pts"):
            f[f"{k}_venue_h"] = _mean([d[k] for d in self.hist_home[home]])
            f[f"{k}_venue_a"] = _mean([d[k] for d in self.hist_away[away]])
        gfh, gfa = f["gf_l10_h"], f["gf_l10_a"]
        f["gfd_l10"] = (gfh - gfa) if (gfh == gfh and gfa == gfa) else NAN
        return f

    def vector(self, f):
        return [f[c] for c in FEATURE_COLS]

    # --- 事後更新（用真實賽果，只喺賽事完場後呼叫） ---
    def update(self, m):
        home, away, div = m["home"], m["away"], m["div"]
        gh, ga = float(m["gh"]), float(m["ga"])
        eh, ea = self.elo[home], self.elo[away]
        lh, la = self.lambdas(div, home, away)
        sc = 1.0 if gh > ga else (0.5 if gh == ga else 0.0)
        exp = 1 / (1 + 10 ** (-(eh + ELO_HOME_ADV - ea) / 400))
        kk = K * (1 + min(abs(gh - ga), 4) * 0.25)
        self.elo[home] = eh + kk * (sc - exp)
        self.elo[away] = ea - kk * (sc - exp)
        self.atk[home] += LR * (gh - lh)
        self.dfn[away] -= LR * (gh - lh)
        self.atk[away] += LR * (ga - la)
        self.dfn[home] -= LR * (ga - la)
        self.mu[div] += 0.0005 * ((gh + ga) - (lh + la))
        self.seen[home] += 1
        self.seen[away] += 1

        def rec(gf, gag, sh, tg, co, ca):
            return {"gf": gf, "ga": gag,
                    "pts": 3.0 if gf > gag else (1.0 if gf == gag else 0.0),
                    "shots": sh, "target": tg, "corners": co, "cards": ca,
                    "gd_h": gf - gag}

        rh = rec(gh, ga, m.get("hs"), m.get("hst"), m.get("hc"), m.get("hy"))
        ra = rec(ga, gh, m.get("as_"), m.get("ast"), m.get("ac"), m.get("ay"))
        self.hist[home].append(rh)
        self.hist[away].append(ra)
        self.hist_home[home].append(rh)
        self.hist_away[away].append(ra)
        day = _ord(m["iso"])
        self.last_day[home] = self.last_day[away] = day
        self.days[home].append(day)
        self.days[away].append(day)
        self.h2h[(home, away) if home < away else (away, home)].append(rh if home < away else ra)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_history():
    """讀自家 data/results/*.csv（football-data.co.uk 欄位），只取賽前可重現嘅欄。"""
    rows = []
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "*.csv"))):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                d = (r.get("Date") or "").strip()
                if len(d) < 8 or not r.get("HomeTeam") or not r.get("AwayTeam"):
                    continue
                dd, mm, yy = d.split("/")
                yy = yy if len(yy) == 4 else ("20" + yy if int(yy) < 50 else "19" + yy)
                try:
                    gh, ga = int(float(r["FTHG"])), int(float(r["FTAG"]))
                except (ValueError, TypeError, KeyError):
                    continue
                rows.append({
                    "iso": f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}",
                    "time": r.get("Time") or "",
                    "div": (r.get("Div") or "").strip(),
                    "home": r["HomeTeam"].strip(),
                    "away": r["AwayTeam"].strip(),
                    "gh": gh, "ga": ga,
                    "hs": _num(r.get("HS")), "as_": _num(r.get("AS")),
                    "hst": _num(r.get("HST")), "ast": _num(r.get("AST")),
                    "hc": _num(r.get("HC")), "ac": _num(r.get("AC")),
                    "hy": _num(r.get("HY")), "ay": _num(r.get("AY")),
                })
    rows.sort(key=lambda x: (x["iso"], x["time"], x["div"], x["home"]))
    return rows


def div_index(rows):
    return {d: i for i, d in enumerate(sorted({r["div"] for r in rows}))}
