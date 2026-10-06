/**
 * 天喜足球雙引擎 dual-v1（定版 2026-09-29）。
 * 引擎 A：凍結三格 p（一分不改）。引擎 B：凍結 λ 上嘅對角膨脹 Poisson 和局機率。
 * 混合：P_D = (1−w)·A_D + w·B_D，主客按 A 比例分返；
 * 加權投票：最高 P_k ÷ 歷史出現率 嗰個結果就係今場雙引擎預測。
 * 常量由 docs/research/football_dual_engine_calibrate.py 用 2014/15–2020/21 定死，
 * 2021/22–2024/25 四季驗證（docs/research/football_dual_engine_v1.json）。一季最多改一次。
 */
export const DUAL_ENGINE = {
  version: "dual-v1",
  frozenAt: "2026-09-29",
  pi: 0.016,
  w: 0.5,
  base: [0.4458, 0.2501, 0.3041] as [number, number, number],
  stake: 10,
  /** 2026-10-06 起新場次平注注碼 $100（模型不變，只升注碼）；舊場次帳面保留 $10 */
  stakeV2: 100,
  stakeV2From: "2026-10-06",
  /** 鎖定線：開賽前 6 小時寫入只增不改帳（2026-10-06 前舊場次為 T−60） */
  lockHours: 6,
} as const;

export type Outcome = "home" | "draw" | "away";
export const OUTCOMES: Outcome[] = ["home", "draw", "away"];
export const OUTCOME_LABEL: Record<Outcome, string> = { home: "主勝", draw: "和局", away: "客勝" };

const MAXG = 10;
function pois(lam: number, k: number) {
  let f = 1;
  for (let i = 2; i <= k; i += 1) f *= i;
  return (Math.exp(-lam) * lam ** k) / f;
}

/** 引擎 B：對角膨脹 Poisson 和局機率 */
export function engineBDraw(lambda: [number, number]) {
  let d = 0;
  for (let k = 0; k <= MAXG; k += 1) d += pois(lambda[0], k) * pois(lambda[1], k);
  return (1 - DUAL_ENGINE.pi) * d + DUAL_ENGINE.pi;
}

export type DualResult = {
  pA: [number, number, number];
  pBDraw: number;
  pFinal: [number, number, number];
  scores: [number, number, number];
  pick: Outcome;
};

export function dualEngine(p: [number, number, number], lambda: [number, number]): DualResult {
  const pBDraw = engineBDraw(lambda);
  const d = (1 - DUAL_ENGINE.w) * p[1] + DUAL_ENGINE.w * pBDraw;
  const ha = p[0] + p[2] || 1;
  const pFinal: [number, number, number] = [((1 - d) * p[0]) / ha, d, ((1 - d) * p[2]) / ha];
  const scores = pFinal.map((v, i) => v / (DUAL_ENGINE.base[i] || 1 / 3)) as [number, number, number];
  const idx = scores.indexOf(Math.max(...scores));
  return { pA: p, pBDraw, pFinal, scores, pick: OUTCOMES[idx] ?? "home" };
}

export const ODDS_SOURCE_LABEL: Record<string, string> = {
  hkjc: "馬會足智彩",
  b365: "Bet365",
  bfe: "Betfair 交易所",
  bv: "BetVictor",
  bw: "Bet&Win",
  bfd: "Betfred",
  pp: "Paddy Power",
  skb: "Sky Bet",
  avg: "市場平均價",
  max: "市場最高價",
  none: "無賠率",
};

/** 平注結算：中 = stake·(odds−1)，輸 = −stake，無賠率 = null（唔入盈虧）。stake 以帳面記錄為準，舊帳預設 $10 */
export function settlePnl(pick: Outcome, ftr: Outcome, pickOdds: number | null, stake: number = DUAL_ENGINE.stake) {
  if (!pickOdds || pickOdds <= 1) return null;
  return pick === ftr ? stake * (pickOdds - 1) : -stake;
}

/** 展示層共用：有 λ 就用雙引擎正式預測，缺 λ 先退回凍結三格最高格（標 isDual=false）。 */
export function dualPick(p: number[], lambda?: number[] | null) {
  if (p.length === 3 && lambda?.length === 2) {
    const r = dualEngine(p as [number, number, number], lambda as [number, number]);
    return { pf: r.pFinal as number[], idx: OUTCOMES.indexOf(r.pick), isDual: true };
  }
  if (p.length !== 3) return { pf: p, idx: -1, isDual: false };
  return { pf: p, idx: p.indexOf(Math.max(...p)), isDual: false };
}
