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
  · S5 集成推論（S4 天喜足球LGB + S3 入球模型 + S2 天喜足球ELO 三軌對數集成 + 向量標度校準）
    由 models/s5 載入；模型 ready 且該場熱身足夠 → status = "final"（綠燈，入公開帳）。
    模型缺失／未過閘／該場未熱身 → 退回 S3+S2 在線混合，status = "fallback"（紅燈，只作診斷）。
  · 波膽同 1X2 必須同一來源：S5 生效時，入球模型比分矩陣按 S5 三個賽果分區重新加權，
    公開嘅比分同賽果永遠一致。
"""
import hashlib, json, math, os, sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import features_s5 as F

ROOT = F.ROOT
S5_DIR = os.path.join(ROOT, "models", "s5")
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
LOCK_MINUTES = 60  # 逐場開賽前幾分鐘鎖定（三份文件統一：黃燈可刷新／綠燈已鎖）


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


def cs_from_matrix(M):
    """由比分矩陣派生波膽四層展示：頭八格、三區條件格、期望比分、尾部桶。"""
    ph, pd_, pa, _, _ = derive(M)
    cells = []
    exp_h = exp_a = 0.0
    win3 = h4 = az = 0.0
    for i in range(len(M)):
        for j in range(len(M[i])):
            pij = M[i][j]
            exp_h += i * pij
            exp_a += j * pij
            if i - j >= 3:
                win3 += pij
            if i >= 4:
                h4 += pij
            if j == 0:
                az += pij
            if i <= 6 and j <= 6:
                cells.append((i, j, pij))
    cells.sort(key=lambda x: x[2], reverse=True)

    def fmt(c):
        return {"score": f"{c[0]}-{c[1]}", "p": round(c[2], 4),
                "res": "home" if c[0] > c[1] else ("draw" if c[0] == c[1] else "away")}

    top8 = [fmt(c) for c in cells[:8]]
    cond = {}
    for res, pres in (("home", ph), ("draw", pd_), ("away", pa)):
        best = next((c for c in cells if (c[0] > c[1] if res == "home" else
                                          (c[0] == c[1] if res == "draw" else c[0] < c[1]))), None)
        if best:
            cond[res] = {"score": f"{best[0]}-{best[1]}", "p": round(best[2], 4),
                         "p_cond": round(best[2] / max(pres, 1e-9), 4)}
    cs = {
        "top8": top8,
        "cond": cond,
        "exp": [round(exp_h, 2), round(exp_a, 2)],
        "tails": {"win_by_3plus": round(win3, 4), "home_4plus": round(h4, 4),
                  "away_clean_sheet": round(az, 4)},
    }
    return top8, cs


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
        top8, cs = cs_from_matrix(M)
        return (ph, pd_, pa), lh, la, over, btts, top8, cs, M


class S5Model:
    """S5 集成推論：LGB + 入球模型 + Elo 三軌對數集成，再做向量標度校準。"""

    def __init__(self):
        self.ready = False
        self.meta = None
        self.booster = None
        try:
            self.meta = json.load(open(os.path.join(S5_DIR, "meta.json"), encoding="utf-8"))
            if not self.meta.get("ready"):
                self.reason = "模型未過三項閘門"
                return
            import lightgbm as lgb  # noqa: WPS433 只喺有模型時才需要
            self.booster = lgb.Booster(model_file=os.path.join(S5_DIR, "lgb.txt"))
            self.ready = True
            self.reason = None
        except Exception as exc:  # 缺模型、缺套件、檔案壞 → 一律退回基準軌，唔靜靜出錯
            self.reason = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _softmax_lr(params, x):
        coef, inter = params["coef"], params["intercept"]
        z = [sum(c * v for c, v in zip(row, x)) + b for row, b in zip(coef, inter)]
        if len(z) == 1:  # 二元退化（理論上唔會發生，保險）
            z = [-z[0], z[0]]
        mx = max(z)
        e = [math.exp(v - mx) for v in z]
        t = sum(e)
        out = [v / t for v in e]
        order = params.get("classes", list(range(len(out))))
        p = [0.0, 0.0, 0.0]
        for cls, v in zip(order, out):
            p[int(cls)] = v
        return p

    def predict(self, feat):
        import numpy as np
        cols = self.meta["feature_cols"]
        x = np.array([[feat[c] for c in cols]], dtype=float)
        p_lgb = [float(v) for v in self.booster.predict(x)[0]]
        p_elo = self._softmax_lr(self.meta["elo_track"], [feat[c] for c in self.meta["elo_cols"]])
        p_dc = [feat["dc_ph"], feat["dc_pd"], feat["dc_pa"]]
        t = sum(p_dc)
        p_dc = [v / t for v in p_dc]
        a = self.meta["alpha"]
        eps = 1e-9
        z = [a["lgb"] * math.log(max(p_lgb[i], eps)) + a["dc"] * math.log(max(p_dc[i], eps))
             + a["elo"] * math.log(max(p_elo[i], eps)) for i in range(3)]
        mx = max(z)
        e = [math.exp(v - mx) for v in z]
        t = sum(e)
        p_ens = [v / t for v in e]
        p_cal = self._softmax_lr(self.meta["calibrator"], [math.log(max(v, eps)) for v in p_ens])
        return p_cal, p_lgb, p_ens


def rescale_matrix(M, p_target):
    """按目標主／和／客機率，逐個賽果分區重新縮放比分矩陣（保證波膽同 1X2 同一來源）。"""
    reg = [0.0, 0.0, 0.0]
    for i, row in enumerate(M):
        for j, v in enumerate(row):
            reg[0 if i > j else (1 if i == j else 2)] += v
    k = [p_target[t] / max(reg[t], 1e-12) for t in range(3)]
    out = [[v * k[0 if i > j else (1 if i == j else 2)] for j, v in enumerate(row)]
           for i, row in enumerate(M)]
    s = sum(sum(r) for r in out)
    return [[v / s for v in r] for r in out]


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
    hist = F.load_history()
    eng = Engine()
    s5 = S5Model()
    feng = F.FeatureEngine()
    div_ids = (s5.meta or {}).get("div_ids", {})
    for m in hist:
        eng.step(m)
        feng.roll_season(m["iso"], m["home"], m["away"])
        feng.update(m)
    today = datetime.now(timezone.utc).date().isoformat()
    n_green = 0

    fx = json.load(open(FIXTURES, encoding="utf-8"))
    now = datetime.now(timezone.utc)
    out_matches = []
    for f in fx.get("fixtures", []):
        home, away, div = f["home"], f["away"], f["div"]
        ready = eng.seen[home] >= WARM and eng.seen[away] >= WARM
        p_elo, elo_diff = eng.elo_probs(home, away)
        p_dc, lh, la, over, btts, scores, cs, M = eng.dc_probs(div, home, away)
        p = blend(p_dc, p_elo) if ready else list(p_elo)
        track = "s3+s2" if ready else "s2"
        status = "fallback"
        p_s5 = p_lgb = None
        s5_warm = (feng.seen[home] >= F.WARM and feng.seen[away] >= F.WARM)
        if s5.ready and s5_warm:
            try:
                feat = feng.features(f.get("date") or today, div, home, away, div_ids)
                p_s5, p_lgb, _ = s5.predict(feat)
                p = list(p_s5)
                track = "s5"
                status = "final"
                # 波膽同 1X2 同一來源：按 S5 三個賽果分區重新加權入球模型矩陣
                M = rescale_matrix(M, p_s5)
                over_new = btts_new = 0.0
                for i in range(len(M)):
                    for j in range(len(M[i])):
                        if i + j >= 3:
                            over_new += M[i][j]
                        if i > 0 and j > 0:
                            btts_new += M[i][j]
                over, btts = over_new, btts_new
                scores, cs = cs_from_matrix(M)
            except Exception as exc:  # 推論失敗即退回基準軌，唔准出半截綠燈
                print(f"WARN: S5 推論失敗 {home} vs {away}: {exc}", file=sys.stderr)
                p_s5 = p_lgb = None
                track = "s3+s2" if ready else "s2"
                status = "fallback"
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
            "track": track,
            "status": status,  # final = 綠燈 S5 集成（入公開帳）／fallback = 紅燈退回基準軌
            "s5_warm": s5_warm,
            "locked": locked,
            "p": [round(v, 4) for v in p],
            "p_dc": [round(v, 4) for v in p_dc],
            "p_elo": [round(v, 4) for v in p_elo],
            "p_s5": [round(v, 4) for v in p_s5] if p_s5 else None,
            "p_lgb": [round(v, 4) for v in p_lgb] if p_lgb else None,
            "lambda": [round(lh, 3), round(la, 3)],
            "over25": round(over, 4),
            "btts": round(btts, 4),
            "top_score": {"score": scores[0]["score"], "p": scores[0]["p"]},
            "scores": scores[:2],
            "cs": cs,
            "elo_diff": round(elo_diff, 1),
            "market": [round(v, 4) for v in market] if market else None,
            "edge": edges,
            "warm": {"home": eng.seen[home], "away": eng.seen[away]},
            "last_match": {"home": eng.last_match.get(home), "away": eng.last_match.get(away)},
        })
        if status == "final":
            n_green += 1

    here = os.path.dirname(os.path.abspath(__file__))
    src = b"".join(open(os.path.join(here, n), "rb").read()
                   for n in ("predict_fixtures.py", "features_s5.py"))
    fp_parts = [hashlib.sha256(src).hexdigest()[:12],
                hashlib.sha256(json.dumps(fx.get("meta", {}), sort_keys=True).encode()).hexdigest()[:12],
                f"{len(hist)}"]
    if s5.ready:
        fp_parts.append("s5:" + str(s5.meta.get("fingerprint", "?")))
    meta = {
        "generated_at": now.isoformat(timespec="seconds"),
        "engine": ("TX-Football S5 三軌集成＋向量標度校準（S4 天喜足球LGB ＋ S3 入球模型 ＋ S2 天喜足球ELO）"
                   if s5.ready else "TX-Football S3+S2 在線混合（S5 集成模型未就緒）"),
        "fingerprint": "-".join(fp_parts),
        "history_matches": len(hist),
        "history_last_date": hist[-1]["iso"] if hist else None,
        "fixtures_count": len(out_matches),
        "fixtures_stale": bool(fx.get("meta", {}).get("stale")),
        "market_beta": 0,
        "weights": {"dc": W_DC, "elo": W_ELO},
        "s5": {
            "ready": bool(s5.ready),
            "reason": None if s5.ready else getattr(s5, "reason", "meta.json 缺失"),
            "fingerprint": (s5.meta or {}).get("fingerprint"),
            "trained_at": (s5.meta or {}).get("trained_at"),
            "alpha": (s5.meta or {}).get("alpha"),
            "gate": ((s5.meta or {}).get("report") or {}).get("gate"),
            "backtest": ((s5.meta or {}).get("report") or {}).get("s5_calibrated"),
            "green_matches": n_green,
        },
        "lock_minutes": LOCK_MINUTES,
        "status_note": (
            f"S5 集成推論已接入：{n_green} 場走綠燈（S4 天喜足球LGB ＋ S3 入球模型 ＋ S2 天喜足球ELO 對數集成，"
            "再做向量標度校準），波膽由同一張比分矩陣按 S5 分區重新加權，賽果同比分永遠一致；"
            "熱身不足或推論失敗嘅場次退回 S3+S2 基準軌，標紅燈，只作診斷、唔入公開帳。"
            if s5.ready else
            "S5 集成模型未就緒（未過三項閘門或缺模型檔），所有場次退回 S3+S2 基準軌，標紅燈，唔當最終預測。"),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump({"meta": meta, "matches": out_matches}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    if not out_matches:
        print("WARN: 冇賽程可預測", file=sys.stderr)


if __name__ == "__main__":
    main()
