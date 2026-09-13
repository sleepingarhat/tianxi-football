#!/usr/bin/env python3
"""S6 賽前預測凍結器（天喜足球）

由自家採集嘅 data/results/*.csv 逐場前推重建兩條在線軌：
  · S2 天喜足球ELO（主客獨立評分）
  · S3 入球模型（Dixon-Coles 在線攻防係數 → 比分機率矩陣）
再喺對數空間加權混合，為 data/fixtures/upcoming.csv 每一場未開賽賽事出機率，
連同版本指紋寫入 data/predictions/upcoming.json。

鐵律：
  · 賠率零權重（market_beta = 0）。賠率只做去水對照同價值注判斷，永不入模。
  · 所有輸入只用開賽前已存在嘅資料（歷史賽果），賽後統計永不入模。
  · 天喜LGB（S4）同集成校準（S5）嘅推論未接入本管線，所以現階段狀態一律
    標「紅燈 · 退回基準」，唔會當最終預測。接入後才會出黃／綠燈。
"""
import csv, glob, hashlib, json, math, os, sys
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "data", "results")
FIXTURES = os.path.join(ROOT, "data", "fixtures", "upcoming.json")
OUT_DIR = os.path.join(ROOT, "data", "predictions")
OUT = os.path.join(OUT_DIR, "upcoming.json")

# --- S2 Elo 參數（同 scripts/elo_s2.py 一致，已凍結） ---
MEAN, HFA, K0, REG = 1500.0, 60.0, 22.0, 0.70
DRAW0, DRAW1 = 0.30, 0.18
# --- S3 入球模型參數（同 scripts/dc_s3.py 一致，已凍結） ---
LR, RHO, GAMMA, REGRESS, WARM, MAXG = 0.04, -0.05, 0.12, 0.80, 40, 10
# --- 混合權重（只有兩軌可用，按回測 RPS 反比取整） ---
W_DC, W_ELO = 0.55, 0.45
LOCK_MINUTES = 30  # 開賽前幾分鐘鎖定


def season_of(y, m):
    return y if m >= 7 else y - 1


def build_matrix(lh, la):
    ph = [math.exp(-lh)]
    pa = [math.exp(-la)]
    for k in range(1, MAXG + 1):
        ph.append(ph[-1] * lh / k)
        pa.append(pa[-1] * la / k)
    M = [[ph[i] * pa[j] for j in range(MAXG + 1)] for i in range(MAXG + 1)]
    tau = {(0, 0): 1 - lh * la * RHO, (0, 1): 1 + lh * RHO,
           (1, 0): 1 + la * RHO, (1, 1): 1 - RHO}
    for (i, j), t in tau.items():
        M[i][j] *= max(t, 1e-9)
    s = sum(sum(r) for r in M)
    return [[v / s for v in r] for r in M]


def derive(M):
    ph = pd_ = pa = over = btts = 0.0
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
            if i > 0 and j > 0:
                btts += p
    return ph, pd_, pa, over, btts


def load_history():
    rows = []
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "*.csv"))):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d = (r.get("Date") or "").strip()
                if len(d) < 8:
                    continue
                dd, mm, yy = d.split("/")
                yy = yy if len(yy) == 4 else ("20" + yy if int(yy) < 50 else "19" + yy)
                try:
                    gh, ga = int(float(r["FTHG"])), int(float(r["FTAG"]))
                except (ValueError, TypeError, KeyError):
                    continue
                if not r.get("HomeTeam") or not r.get("AwayTeam"):
                    continue
                rows.append({
                    "iso": f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}",
                    "time": (r.get("Time") or ""),
                    "div": r.get("Div", ""),
                    "home": r["HomeTeam"].strip(),
                    "away": r["AwayTeam"].strip(),
                    "gh": gh, "ga": ga,
                })
    rows.sort(key=lambda x: (x["iso"], x["time"], x["div"]))
    return rows


class Engine:
    """逐場前推維護 Elo 同攻防係數；跑完歷史即係「開賽前」狀態。"""

    def __init__(self):
        self.hr = defaultdict(lambda: MEAN)
        self.ar = defaultdict(lambda: MEAN)
        self.last_season_team = {}
        self.atk = defaultdict(float)
        self.dfn = defaultdict(float)
        self.base = defaultdict(lambda: math.log(1.35))
        self.seen = defaultdict(int)
        self.last_season_div = {}
        self.last_match = {}

    def step(self, m):
        s = season_of(int(m["iso"][:4]), int(m["iso"][5:7]))
        for team in (m["home"], m["away"]):
            if self.last_season_team.get(team) not in (None, s):
                self.hr[team] = MEAN + REG * (self.hr[team] - MEAN)
                self.ar[team] = MEAN + REG * (self.ar[team] - MEAN)
            self.last_season_team[team] = s
        d = m["div"]
        if self.last_season_div.get(d) != s:
            self.last_season_div[d] = s
            for t in list(self.atk):
                self.atk[t] *= REGRESS
                self.dfn[t] *= REGRESS

        diff = (self.hr[m["home"]] + HFA) - self.ar[m["away"]]
        eh = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        res = m["gh"] - m["ga"]
        score_h = 1.0 if res > 0 else (0.5 if res == 0 else 0.0)
        gd = abs(res)
        km = 1.0 if gd <= 1 else (1.5 if gd == 2 else (1.75 if gd == 3 else 2.0))
        delta = K0 * km * (score_h - eh)
        self.hr[m["home"]] += delta
        self.ar[m["away"]] -= delta

        lh = min(max(math.exp(self.base[d] + GAMMA + self.atk[m["home"]] - self.dfn[m["away"]]), 0.15), 6.0)
        la = min(max(math.exp(self.base[d] - GAMMA + self.atk[m["away"]] - self.dfn[m["home"]]), 0.15), 6.0)
        eh_g, ea_g = m["gh"] - lh, m["ga"] - la
        self.atk[m["home"]] += LR * eh_g
        self.dfn[m["away"]] -= LR * eh_g
        self.atk[m["away"]] += LR * ea_g
        self.dfn[m["home"]] -= LR * ea_g
        for t in (m["home"], m["away"]):
            self.atk[t] = min(max(self.atk[t], -1.2), 1.2)
            self.dfn[t] = min(max(self.dfn[t], -1.2), 1.2)
        self.base[d] += 0.002 * ((m["gh"] + m["ga"]) - (lh + la)) / max(lh + la, 0.5)
        self.seen[m["home"]] += 1
        self.seen[m["away"]] += 1
        self.last_match[m["home"]] = m["iso"]
        self.last_match[m["away"]] = m["iso"]

    def elo_probs(self, home, away):
        diff = (self.hr[home] + HFA) - self.ar[away]
        eh = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        ea = 1.0 - eh
        pd_ = max(0.10, DRAW0 - DRAW1 * abs(eh - ea))
        return ((1 - pd_) * eh, pd_, (1 - pd_) * ea), diff

    def dc_probs(self, div, home, away):
        lh = min(max(math.exp(self.base[div] + GAMMA + self.atk[home] - self.dfn[away]), 0.15), 6.0)
        la = min(max(math.exp(self.base[div] - GAMMA + self.atk[away] - self.dfn[home]), 0.15), 6.0)
        M = build_matrix(lh, la)
        ph, pd_, pa, over, btts = derive(M)
        top = max(
            ((i, j, M[i][j]) for i in range(6) for j in range(6)),
            key=lambda x: x[2],
        )
        return (ph, pd_, pa), lh, la, over, btts, {"score": f"{top[0]}-{top[1]}", "p": top[2]}


def blend(p_dc, p_elo):
    eps = 1e-9
    z = [W_DC * math.log(max(a, eps)) + W_ELO * math.log(max(b, eps)) for a, b in zip(p_dc, p_elo)]
    mx = max(z)
    e = [math.exp(v - mx) for v in z]
    s = sum(e)
    return [v / s for v in e]


def devig(oh, od, oa):
    try:
        inv = [1 / float(oh), 1 / float(od), 1 / float(oa)]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    s = sum(inv)
    if not (1.0 < s < 1.5):
        return None
    return [v / s for v in inv]


def main():
    hist = load_history()
    eng = Engine()
    for m in hist:
        eng.step(m)

    fx = json.load(open(FIXTURES, encoding="utf-8"))
    now = datetime.now(timezone.utc)
    out_matches = []
    for f in fx.get("fixtures", []):
        home, away, div = f["home"], f["away"], f["div"]
        ready = eng.seen[home] >= WARM and eng.seen[away] >= WARM
        p_elo, elo_diff = eng.elo_probs(home, away)
        p_dc, lh, la, over, btts, top_score = eng.dc_probs(div, home, away)
        p = blend(p_dc, p_elo) if ready else list(p_elo)
        market = devig(f.get("odds_h"), f.get("odds_d"), f.get("odds_a"))
        edges = None
        if market:
            edges = [round(p[i] - market[i], 4) for i in range(3)]
        ko = f.get("kickoff_utc") or ""
        locked = False
        try:
            kt = datetime.fromisoformat(ko.replace("+00:00", "")).replace(tzinfo=timezone.utc)
            locked = (kt - now).total_seconds() <= LOCK_MINUTES * 60
        except ValueError:
            kt = None
        out_matches.append({
            "match_key": f["match_key"],
            "div": div,
            "league_zh": f.get("league_zh", div),
            "home": home,
            "away": away,
            "kickoff_utc": ko,
            "time_uk": f.get("time_uk", ""),
            "track": "s3+s2" if ready else "s2",
            "status": "fallback",  # LGB/集成推論未接入，一律紅燈退回基準
            "locked": locked,
            "p": [round(v, 4) for v in p],
            "p_dc": [round(v, 4) for v in p_dc],
            "p_elo": [round(v, 4) for v in p_elo],
            "lambda": [round(lh, 3), round(la, 3)],
            "over25": round(over, 4),
            "btts": round(btts, 4),
            "top_score": {"score": top_score["score"], "p": round(top_score["p"], 4)},
            "elo_diff": round(elo_diff, 1),
            "market": [round(v, 4) for v in market] if market else None,
            "edge": edges,
            "warm": {"home": eng.seen[home], "away": eng.seen[away]},
            "last_match": {"home": eng.last_match.get(home), "away": eng.last_match.get(away)},
        })

    src = open(os.path.abspath(__file__), "rb").read()
    fp_parts = [hashlib.sha256(src).hexdigest()[:12],
                hashlib.sha256(json.dumps(fx.get("meta", {}), sort_keys=True).encode()).hexdigest()[:12],
                f"{len(hist)}"]
    meta = {
        "generated_at": now.isoformat(timespec="seconds"),
        "engine": "TX-Football S3+S2 在線混合（S4 LGB／S5 集成推論未接入）",
        "fingerprint": "-".join(fp_parts),
        "history_matches": len(hist),
        "history_last_date": hist[-1]["iso"] if hist else None,
        "fixtures_count": len(out_matches),
        "fixtures_stale": bool(fx.get("meta", {}).get("stale")),
        "market_beta": 0,
        "weights": {"dc": W_DC, "elo": W_ELO},
        "lock_minutes": LOCK_MINUTES,
        "status_note": "S4 天喜LGB 同 S5 集成校準嘅推論未接入本管線，所有場次一律標「紅燈 · 退回基準」，唔當最終預測。",
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump({"meta": meta, "matches": out_matches}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    if not out_matches:
        print("WARN: 冇賽程可預測", file=sys.stderr)


if __name__ == "__main__":
    main()
