/**
 * 即時賽果專用通道嘅名稱對碰（純展示層）。
 * 上游週批 CSV（football-data.co.uk）仲未出賽果時，用即時源嘅 90 分鐘完場比分先行結算，
 * 並標明「即時源」。凍結預測、λ、矩陣、指紋、盤口權重一律唔動。
 */
export type LiveResult = {
  div: string;
  kickoff_utc: string;
  home: string;
  away: string;
  ft_h: number;
  ft_a: number;
  ftr: "home" | "draw" | "away";
  source: string;
};

/** football-data.co.uk 簡寫 → 即時源全名（只列容易對唔上嘅） */
const ALIAS: Record<string, string> = {
  "man united": "manchester united",
  "man city": "manchester city",
  "nottm forest": "nottingham forest",
  "nott m forest": "nottingham forest",
  wolves: "wolverhampton wanderers",
  newcastle: "newcastle united",
  leeds: "leeds united",
  brighton: "brighton hove albion",
  tottenham: "tottenham hotspur",
  "sheffield united": "sheffield united",
  "west ham": "west ham united",
  "ath bilbao": "athletic",
  "ath madrid": "atletico madrid",
  espanol: "espanyol",
  sociedad: "real sociedad",
  vallecano: "rayo vallecano",
  celta: "celta vigo",
  betis: "real betis",
  "ein frankfurt": "eintracht frankfurt",
  "bayern munich": "bayern munchen",
  "m gladbach": "borussia monchengladbach",
  mgladbach: "borussia monchengladbach",
  dortmund: "borussia dortmund",
  leverkusen: "bayer leverkusen",
  "fc koln": "koln",
  hertha: "hertha berlin",
  inter: "internazionale",
  milan: "ac milan",
  verona: "hellas verona",
  "paris sg": "paris saint germain",
  psg: "paris saint germain",
  "st etienne": "saint etienne",
};

const STOP = new Set([
  "fc", "cf", "afc", "sc", "ac", "as", "ss", "ssc", "us", "sv", "tsg", "vfl", "vfb", "bsc",
  "club", "calcio", "balompie", "de", "the", "1899", "1900", "04", "05", "1846", "1893",
  "deportivo", "cd", "ud", "rcd", "rc", "cp", "sd", "ca", "olympique", "olympiques",
]);

export function normName(raw: string): string {
  const base = raw
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
  return ALIAS[base] ?? base;
}

function tokens(raw: string): string[] {
  return normName(raw)
    .split(" ")
    .filter((t) => t.length > 1 && !STOP.has(t));
}

/** 0–1 相似度：token 前綴互相覆蓋比率 */
export function nameScore(a: string, b: string): number {
  const ta = tokens(a);
  const tb = tokens(b);
  if (!ta.length || !tb.length) return 0;
  const hit = (xs: string[], ys: string[]) =>
    xs.filter((x) => ys.some((y) => x === y || (x.length >= 4 && (y.startsWith(x) || x.startsWith(y))))).length;
  return Math.max(hit(ta, tb) / ta.length, hit(tb, ta) / tb.length);
}

const WINDOW_MS = 30 * 60 * 60 * 1000;
const THRESHOLD = 0.6;

/** 為一條凍結列找即時賽果：同聯賽、開賽時間相差 30 小時內、主客名稱同時對得上 */
export function matchLive(
  rec: { div: string; home: string; away: string; kickoff_utc?: string },
  pool: LiveResult[],
): LiveResult | null {
  const ko = rec.kickoff_utc ? Date.parse(rec.kickoff_utc) : NaN;
  let best: LiveResult | null = null;
  let bestScore = 0;
  for (const m of pool) {
    if (m.div !== rec.div) continue;
    if (Number.isFinite(ko)) {
      const dt = Math.abs(Date.parse(m.kickoff_utc) - ko);
      if (!Number.isFinite(dt) || dt > WINDOW_MS) continue;
    }
    const s = Math.min(nameScore(rec.home, m.home), nameScore(rec.away, m.away));
    if (s >= THRESHOLD && s > bestScore) {
      best = m;
      bestScore = s;
    }
  }
  return best;
}

/** 三格機率 vs 實際賽果嘅 Ranked Probability Score（同後端定義一致） */
export function rpsOf(p: [number, number, number], ftr: "home" | "draw" | "away"): number {
  const act = ftr === "home" ? [1, 0, 0] : ftr === "draw" ? [0, 1, 0] : [0, 0, 1];
  let cp = 0;
  let ca = 0;
  let sum = 0;
  for (let i = 0; i < 2; i += 1) {
    cp += p[i]!;
    ca += act[i]!;
    sum += (cp - ca) ** 2;
  }
  return sum / 2;
}
