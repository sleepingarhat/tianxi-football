import csv, json, math
from collections import defaultdict

MEAN = 1500.0
HFA = 80.0          # 主場優勢（Elo 分）
K0 = 24.0           # 基礎 K
REG = 0.90          # 跨季回歸保留比例
DRAW0, DRAW1 = 0.30, 0.18   # 和局機率模型參數

rows = []
with open('/tmp/fb/Matches.csv', newline='') as f:
    for r in csv.DictReader(f):
        try:
            hg, ag = int(float(r['FTHome'])), int(float(r['FTAway']))
        except (ValueError, TypeError):
            continue
        rows.append({
            'date': r['MatchDate'], 'div': r['Division'],
            'home': r['HomeTeam'], 'away': r['AwayTeam'],
            'hg': hg, 'ag': ag,
        })
rows.sort(key=lambda x: (x['date'], x['div']))
print('matches:', len(rows))

# 主客獨立評分
hr, ar = defaultdict(lambda: MEAN), defaultdict(lambda: MEAN)
last_season = {}

def season_of(date_str):
    y, m = int(date_str[:4]), int(date_str[5:7])
    return y if m >= 7 else y - 1   # 七月起算新季

def k_mult(gd):
    return 1.0 if gd <= 1 else (1.5 if gd == 2 else (1.75 if gd == 3 else 2.0))

sum_rps = sum_ll = 0.0
n = hit = 0
per_league = defaultdict(lambda: [0.0, 0.0, 0, 0])

for m in rows:
    s = season_of(m['date'])
    for team, store in ((m['home'], hr), (m['away'], ar)):
        if last_season.get(team) is not None and last_season[team] != s:
            hr[team] = MEAN + REG * (hr[team] - MEAN)
            ar[team] = MEAN + REG * (ar[team] - MEAN)
        last_season[team] = s

    diff = (hr[m['home']] + HFA) - ar[m['away']]
    eh = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    ea = 1.0 - eh
    r_ = eh - ea
    pd = max(0.10, DRAW0 - DRAW1 * abs(r_))
    ph, pa = (1 - pd) * eh, (1 - pd) * ea

    res = m['hg'] - m['ag']
    y = (1.0 if res > 0 else 0.0, 1.0 if res == 0 else 0.0, 1.0 if res < 0 else 0.0)
    p = (ph, pd, pa)
    rps = 0.5 * sum((sum(p[:i+1]) - sum(y[:i+1])) ** 2 for i in range(3))
    eps = 1e-12
    ll = -math.log(max(eps, p[0] if res > 0 else (p[1] if res == 0 else p[2])))
    pred = p.index(max(p))
    actual = 0 if res > 0 else (1 if res == 0 else 2)
    sum_rps += rps; sum_ll += ll; n += 1; hit += (pred == actual)
    pl = per_league[m['div']]
    pl[0] += rps; pl[1] += ll; pl[2] += 1; pl[3] += (pred == actual)

    # 賽後更新（賽後先知嘅嘢唔入機率，只入之後嘅評分）
    score_h = 1.0 if res > 0 else (0.5 if res == 0 else 0.0)
    km = k_mult(abs(res))
    delta = K0 * km * (score_h - eh)
    hr[m['home']] += delta
    ar[m['away']] -= delta

out = {
    'matches': n,
    'rps': round(sum_rps / n, 4),
    'logloss': round(sum_ll / n, 4),
    'accuracy': round(hit / n * 100, 2),
    'params': {'hfa': HFA, 'k0': K0, 'reg': REG, 'draw0': DRAW0, 'draw1': DRAW1, 'mean': MEAN},
    'leagues': {k: {'n': v[2], 'rps': round(v[0]/v[2], 4), 'logloss': round(v[1]/v[2], 4), 'acc': round(v[3]/v[2]*100, 2)} for k, v in sorted(per_league.items())},
}
with open('/tmp/fb/elo_s2.json', 'w') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(json.dumps({k: v for k, v in out.items() if k != 'leagues'}, ensure_ascii=False, indent=1))
