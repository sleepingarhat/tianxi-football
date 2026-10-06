import records from "@/data/football-weight-study-2425.json";

export type StudyMatch = Omit<(typeof records)[number], "result" | "div" | "score" | "goals" | "form" | "p" | "odds"> & {
  result: "H" | "D" | "A";
  div: string;
  score: [number, number]; goals: [number, number]; form: [number[], number[]]; p: [number, number, number]; odds: [number, number, number] | null;
};
export const studyMatches = records as StudyMatch[];
export type Weights = { strength: number; venue: number; form: number; injury: number };
export const DEFAULT_WEIGHTS: Weights = { strength: 1, venue: 1, form: 1, injury: 0 };
export const LABELS = ["主勝", "和局", "客勝"] as const;
export const LEAGUES: Record<string, string> = { E0: "英超", SP1: "西甲", D1: "德甲", I1: "意甲", F1: "法甲" };

/** Exploratory scenario around a frozen season-out 1X2 proxy, not S5 or a re-trained model. */
export function scenario(match: StudyMatch, weights: Weights) {
  const gap = Math.max(-2, Math.min(2, match.eloGap / 200));
  const homeForm = match.form[0].reduce((sum, n) => sum + n, 0) / Math.max(1, match.form[0].length);
  const awayForm = match.form[1].reduce((sum, n) => sum + n, 0) / Math.max(1, match.form[1].length);
  const formDiff = Math.max(-1, Math.min(1, (homeForm - awayForm) / 2));
  const strength = (weights.strength - 1) * gap * 0.38;
  const venue = (weights.venue - 1) * 0.15;
  const form = (weights.form - 1) * formDiff * 0.22;
  // Injury snapshots are absent; injury weight is intentionally inert.
  const logits = match.p.map((p, i) => Math.log(Math.max(p, 1e-8)) + (i === 0 ? 1 : i === 2 ? -1 : 0) * (strength + venue + form));
  const maximum = Math.max(...logits);
  const exp = logits.map((value) => Math.exp(value - maximum));
  const total = exp.reduce((sum, value) => sum + value, 0);
  const p = exp.map((value) => value / total) as [number, number, number];
  const qRaw = match.odds?.map((odd) => 1 / odd);
  const qTotal = qRaw?.reduce((sum, value) => sum + value, 0);
  const q = qRaw && qTotal ? qRaw.map((value) => value / qTotal) as [number, number, number] : null;
  const edge = q ? p.map((value, i) => value - q[i as 0 | 1 | 2]) as [number, number, number] : null;
  const pick = edge ? edge.indexOf(Math.max(...edge)) : null;
  const result = { H: 0, D: 1, A: 2 }[match.result];
  const selected = pick !== null && edge !== null && (edge[pick] ?? 0) >= 0.05;
  const profit = selected && match.odds && pick !== null ? (pick === result ? (match.odds[pick] ?? 1) - 1 : -1) : null;
  const expectedValue = pick !== null && match.odds ? (p[pick] ?? 0) * (match.odds[pick] ?? 0) - 1 : null;
  const outcome: [number, number, number] = [Number(result === 0), Number(result === 1), Number(result === 2)];
  const rps = ((p[0] - outcome[0]) ** 2 + (p[0] + p[1] - outcome[0] - outcome[1]) ** 2) / 2;
  return { p, edge, pick, selected, profit, expectedValue, rps, strength, venue, form };
}