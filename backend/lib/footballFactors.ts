/** 足球因子目錄（研究展示層）：全部由 T−60 凍結帳＋BSD 賽前快照派生，唔改凍結預測，權重 0。 */
export type Ctx = {
  round?: string | null;
  weather?: { description?: string; temperature_c?: number; wind_speed?: number; code?: number } | null;
  pitch_condition?: number | null;
  travel_km?: number | null;
  derby?: boolean | null;
  neutral?: boolean | null;
  h2h?: { total_matches?: number; home_wins?: number; draws?: number; away_wins?: number; avg_total_goals?: number; recent_matches?: { date: string; home: string; away: string; score: string }[] } | null;
  referee?: { name?: string; matches?: number; avg_yellow_per_match?: number; avg_red_per_match?: number; avg_goals_per_match?: number; avg_fouls_per_match?: number } | null;
  odds?: { home_win?: number; draw?: number; away_win?: number; over_25_goals?: number; under_25_goals?: number; btts_yes?: number; btts_no?: number } | null;
  odds_at?: string | null;
};
export type Led = { p_final: number[]; p_a: number[]; p_b_d: number; lambda: number[]; prediction: string };
export type Snap = { context?: unknown; unavailable?: unknown; lineups?: unknown; bsd_prediction?: unknown } | null;

export type FactorDef = {
  key: string; label: string; group: "天喜凍結" | "外部模型" | "賽前情境" | "球證" | "對賽往績" | "陣容" | "市場（只記帳）";
  unit?: string; digits?: number;
  get: (l: Led | null, s: Snap) => number | null;
};

const ctx = (s: Snap) => (s?.context ?? null) as Ctx | null;
const bsd = (s: Snap) => (s?.bsd_prediction ?? null) as Record<string, number> | null;
const unav = (s: Snap) => (Array.isArray(s?.unavailable) ? (s!.unavailable as unknown[]) : null);
const imp = (o?: number) => (o && o > 1 ? 1 / o : null);

export const FACTORS: FactorDef[] = [
  { key: "pH", label: "主勝機率", group: "天喜凍結", unit: "%", get: (l) => (l ? l.p_final[0]! * 100 : null) },
  { key: "pD", label: "和局機率", group: "天喜凍結", unit: "%", get: (l) => (l ? l.p_final[1]! * 100 : null) },
  { key: "pA", label: "客勝機率", group: "天喜凍結", unit: "%", get: (l) => (l ? l.p_final[2]! * 100 : null) },
  { key: "gap", label: "主客強弱差", group: "天喜凍結", unit: "%", get: (l) => (l ? (l.p_final[0]! - l.p_final[2]!) * 100 : null) },
  { key: "pBD", label: "引擎 B 和率", group: "天喜凍結", unit: "%", get: (l) => (l ? l.p_b_d * 100 : null) },
  { key: "lamH", label: "主隊 λ", group: "天喜凍結", digits: 2, get: (l) => l?.lambda[0] ?? null },
  { key: "lamA", label: "客隊 λ", group: "天喜凍結", digits: 2, get: (l) => l?.lambda[1] ?? null },
  { key: "lamT", label: "合計 λ", group: "天喜凍結", digits: 2, get: (l) => (l ? l.lambda[0]! + l.lambda[1]! : null) },
  { key: "bH", label: "外部主勝", group: "外部模型", unit: "%", get: (_l, s) => bsd(s)?.["prob_home_win"] ?? null },
  { key: "bD", label: "外部和局", group: "外部模型", unit: "%", get: (_l, s) => bsd(s)?.["prob_draw"] ?? null },
  { key: "bA", label: "外部客勝", group: "外部模型", unit: "%", get: (_l, s) => bsd(s)?.["prob_away_win"] ?? null },
  { key: "bO25", label: "外部大 2.5", group: "外部模型", unit: "%", get: (_l, s) => bsd(s)?.["prob_over_25"] ?? null },
  { key: "bBTTS", label: "外部兩隊入球", group: "外部模型", unit: "%", get: (_l, s) => bsd(s)?.["prob_btts_yes"] ?? null },
  { key: "temp", label: "氣溫", group: "賽前情境", unit: "°C", get: (_l, s) => ctx(s)?.weather?.temperature_c ?? null },
  { key: "wind", label: "風速", group: "賽前情境", unit: "m/s", digits: 1, get: (_l, s) => ctx(s)?.weather?.wind_speed ?? null },
  { key: "pitch", label: "場地狀況", group: "賽前情境", get: (_l, s) => ctx(s)?.pitch_condition ?? null },
  { key: "travel", label: "客隊長途", group: "賽前情境", unit: "km", get: (_l, s) => ctx(s)?.travel_km ?? null },
  { key: "derby", label: "同城打吡", group: "賽前情境", get: (_l, s) => (ctx(s)?.derby == null ? null : ctx(s)!.derby ? 1 : 0) },
  { key: "neutral", label: "中立場", group: "賽前情境", get: (_l, s) => (ctx(s)?.neutral == null ? null : ctx(s)!.neutral ? 1 : 0) },
  { key: "refY", label: "球證場均黃牌", group: "球證", digits: 2, get: (_l, s) => ctx(s)?.referee?.avg_yellow_per_match ?? null },
  { key: "refR", label: "球證場均紅牌", group: "球證", digits: 2, get: (_l, s) => ctx(s)?.referee?.avg_red_per_match ?? null },
  { key: "refG", label: "球證場均入球", group: "球證", digits: 2, get: (_l, s) => ctx(s)?.referee?.avg_goals_per_match ?? null },
  { key: "refF", label: "球證場均犯規", group: "球證", digits: 1, get: (_l, s) => ctx(s)?.referee?.avg_fouls_per_match ?? null },
  { key: "h2hN", label: "對賽場數", group: "對賽往績", get: (_l, s) => ctx(s)?.h2h?.total_matches ?? null },
  { key: "h2hD", label: "對賽和局率", group: "對賽往績", unit: "%", get: (_l, s) => { const h = ctx(s)?.h2h; return h?.total_matches ? ((h.draws ?? 0) / h.total_matches) * 100 : null; } },
  { key: "h2hG", label: "對賽場均入球", group: "對賽往績", digits: 2, get: (_l, s) => ctx(s)?.h2h?.avg_total_goals ?? null },
  { key: "unav", label: "預計缺陣人數", group: "陣容", get: (_l, s) => unav(s)?.length ?? null },
  { key: "conf", label: "主隊陣容可信度", group: "陣容", unit: "%", get: (_l, s) => { const c = (s?.lineups as { home?: { confidence?: number } } | null)?.home?.confidence; return c == null ? null : c * 100; } },
  { key: "oH", label: "開盤主勝隱含", group: "市場（只記帳）", unit: "%", get: (_l, s) => { const v = imp(ctx(s)?.odds?.home_win); return v == null ? null : v * 100; } },
  { key: "oD", label: "開盤和局隱含", group: "市場（只記帳）", unit: "%", get: (_l, s) => { const v = imp(ctx(s)?.odds?.draw); return v == null ? null : v * 100; } },
  { key: "oA", label: "開盤客勝隱含", group: "市場（只記帳）", unit: "%", get: (_l, s) => { const v = imp(ctx(s)?.odds?.away_win); return v == null ? null : v * 100; } },
];

export const fmtFactor = (f: FactorDef, v: number | null) => {
  if (v == null || Number.isNaN(v)) return "–";
  if (f.key === "derby" || f.key === "neutral") return v ? "是" : "否";
  return `${v.toFixed(f.digits ?? 0)}${f.unit ? (f.unit === "%" ? "%" : ` ${f.unit}`) : ""}`;
};

export const WEATHER_ZH: Record<string, string> = { clear: "晴", cloudy: "多雲", "partly cloudy": "部分多雲", rain: "雨", "light rain": "微雨", snow: "雪", fog: "霧", overcast: "陰天", drizzle: "毛毛雨", thunderstorm: "雷暴" };
export const PITCH_ZH = (n?: number | null) => (n == null ? "–" : ["–", "良好", "一般", "濕滑", "惡劣"][n] ?? `等級 ${n}`);
