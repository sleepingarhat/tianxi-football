import { createFileRoute } from "@tanstack/react-router";

/**
 * 足球即時戰況（文字直播資料源）。
 * 資料：GOAL API（https://goal-api.com）免費層，伺服器端輪詢＋記憶體暫存，
 * 瀏覽器只打我哋呢個端點，唔會直接暴露 API key。
 * 純展示層：唔寫入任何凍結資料，唔影響預測、指紋、對帳。
 */

const GOAL_BASE = "https://api.goal-api.com/v1";
const CACHE_TTL_MS = 30_000;
const EVENT_MATCH_LIMIT = 12;

/** 我哋覆蓋嘅五大聯賽：GOAL leagueName 關鍵字 → 內部聯賽代碼（同 football-data.co.uk 一致） */
const LEAGUE_MAP: [RegExp, string, string][] = [
  [/premier league/i, "E0", "英超"],
  [/la liga|primera/i, "SP1", "西甲"],
  [/serie a/i, "I1", "意甲"],
  [/bundesliga/i, "D1", "德甲"],
  [/ligue 1/i, "F1", "法甲"],
];

export type LiveEvent = {
  minute: number | null;
  type: "goal" | "sub" | "card" | "other";
  team: string | null;
  player: string | null;
  detail: string | null;
};

export type LiveMatch = {
  id: string;
  div: string;
  league_zh: string;
  home: string;
  away: string;
  home_score: number;
  away_score: number;
  status: string;
  period: string | null;
  elapsed: number | null;
  kickoff_utc: string | null;
  events: LiveEvent[];
};

type Cache = { at: number; payload: unknown } | null;
let cache: Cache = null;

function mapLeague(name: string | undefined): { div: string; zh: string } | null {
  if (!name) return null;
  for (const [re, div, zh] of LEAGUE_MAP) if (re.test(name)) return { div, zh };
  return null;
}

function normEvent(raw: any): LiveEvent {
  const t = String(raw?.type ?? raw?.eventType ?? "").toLowerCase();
  const type: LiveEvent["type"] = t.includes("goal")
    ? "goal"
    : t.includes("sub")
      ? "sub"
      : t.includes("card")
        ? "card"
        : "other";
  // GOAL API 事件欄位：time/timeNum 係分鐘，入球睇 homeScorer／awayScorer 邊邊有名
  const minuteRaw = raw?.timeNum ?? raw?.time ?? raw?.minute ?? raw?.elapsed ?? null;
  const minute =
    typeof minuteRaw === "number" ? minuteRaw : Number.parseInt(String(minuteRaw ?? ""), 10);
  const scorer: string | null = raw?.homeScorer ?? raw?.awayScorer ?? raw?.playerName ?? null;
  const subIn: string | null = raw?.playerIn ?? raw?.player ?? null;
  const subOut: string | null = raw?.playerOut ?? null;
  const carded: string | null = raw?.playerName ?? raw?.player ?? null;
  const who =
    type === "sub" && subIn
      ? subOut
        ? `${subIn} 替換 ${subOut}`
        : subIn
      : type === "card"
        ? carded
        : scorer;
  return {
    minute: Number.isFinite(minute) ? (minute as number) : null,
    type,
    team: raw?.teamName ?? raw?.team ?? null,
    player: who,
    detail: raw?.info ?? (type === "goal" ? (raw?.score ?? null) : (raw?.description ?? null)),
  };
}

async function goalGet(path: string, key: string): Promise<any> {
  const r = await fetch(`${GOAL_BASE}${path}`, {
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
  });
  if (!r.ok) throw new Error(`GOAL API ${r.status} ${path}`);
  return r.json();
}

export const Route = createFileRoute("/api/public/football-live")({
  server: {
    handlers: {
      GET: async () => {
        const key = process.env["GOAL_API_KEY"];
        if (!key) {
          return Response.json({
            configured: false,
            note: "未設定 GOAL_API_KEY，即時戰況暫停",
            matches: [],
            generated_at: new Date().toISOString(),
          });
        }
        if (cache && Date.now() - cache.at < CACHE_TTL_MS) {
          return Response.json(cache.payload);
        }
        try {
          const live = await goalGet("/fixtures/live?limit=100", key);
          const rows: any[] = Array.isArray(live?.data) ? live.data : [];
          const matches: LiveMatch[] = [];
          for (const r of rows) {
            const lg = mapLeague(r?.leagueName);
            if (!lg) continue;
            matches.push({
              id: String(r?.id ?? ""),
              div: lg.div,
              league_zh: lg.zh,
              home: String(r?.homeTeamName ?? ""),
              away: String(r?.awayTeamName ?? ""),
              home_score: Number.parseInt(String(r?.homeTeamScore ?? "0"), 10) || 0,
              away_score: Number.parseInt(String(r?.awayTeamScore ?? "0"), 10) || 0,
              status: String(r?.matchStatus ?? "LIVE"),
              period: r?.matchPeriod ?? null,
              elapsed: typeof r?.matchElapsed === "number" ? r.matchElapsed : null,
              kickoff_utc: r?.kickoffUtc ?? null,
              events: [],
            });
          }
          // 逐場事件（入球／換人／牌）：限量拉取，失敗唔影響比分
          for (const m of matches.slice(0, EVENT_MATCH_LIMIT)) {
            try {
              const ev = await goalGet(`/fixtures/${m.id}/events?limit=50`, key);
              const list: any[] = Array.isArray(ev?.data) ? ev.data : [];
              m.events = list
                .map(normEvent)
                .filter((e) => e.type !== "other")
                .sort((a, b) => (b.minute ?? 0) - (a.minute ?? 0));
            } catch {
              m.events = [];
            }
          }
          const payload = {
            configured: true,
            matches,
            generated_at: new Date().toISOString(),
          };
          cache = { at: Date.now(), payload };
          return Response.json(payload);
        } catch (e) {
          // 上游失手時回舊暫存（如有），唔好令版面消失
          if (cache) return Response.json({ ...(cache.payload as object), stale: true });
          return Response.json({
            configured: true,
            error: e instanceof Error ? e.message : "上游暫時唔得",
            matches: [],
            generated_at: new Date().toISOString(),
          });
        }
      },
    },
  },
});
