import { createFileRoute } from "@tanstack/react-router";

/**
 * 官方隊徽 URL 代理（第一順位源：football-data.org 免費層）
 * - 只回傳對方託管嘅 crest URL，唔會自己託管商標圖檔
 * - 免費層 rate limit 每分鐘 10 次，所以逐個聯賽獨立快取（邊緣 7 日）
 * - 免費層未覆蓋嘅聯賽回 { ok:true, teams:[] }，前端無縫退回派生盾形識別標
 */
const COMP: Record<string, string> = {
  E0: "PL", // 英超
  E1: "ELC", // 英冠
  D1: "BL1", // 德甲
  I1: "SA", // 意甲
  SP1: "PD", // 西甲
  F1: "FL1", // 法甲
  N1: "DED", // 荷甲
  P1: "PPL", // 葡超
};

/** 第二順位源：API-Football（已授權帳戶）。football-data.org 免費層未覆蓋嘅聯賽用呢個補。 */
const AF_LEAGUE: Record<string, number> = {
  E0: 39, E1: 40, E2: 41, E3: 42, EC: 43,
  SC0: 179, SC1: 180, SC2: 181, SC3: 182,
  D1: 78, D2: 79, I1: 135, I2: 136, SP1: 140, SP2: 141, F1: 61, F2: 62,
  N1: 88, B1: 144, P1: 94, T1: 203, G1: 197,
  AUT: 218, SWZ: 207, DNK: 119, NOR: 103, SWE: 113, FIN: 244, POL: 106,
  ROU: 283, RUS: 235, IRL: 357, USA: 253, MEX: 262, BRA: 71, ARG: 128,
  CHN: 169, JPN: 98,
};

type Team = { name: string; short: string; tla: string; crest: string };

/** 香港時間七月起算新賽季，同引擎口徑一致 */
function seasonOf(now = new Date()) {
  return now.getUTCMonth() + 1 >= 7 ? now.getUTCFullYear() : now.getUTCFullYear() - 1;
}

/** 隊徽 URL 長期穩定，賽季只係查詢維度；免費層只開到 2024，所以當季唔得就退返 2024。 */
async function fromApiFootball(div: string): Promise<Team[]> {
  const league = AF_LEAGUE[div];
  const key = process.env["APISPORTS_API_FOOTBALL_KEY"] ?? process.env["API_FOOTBALL_KEY"];
  if (!league || !key) return [];
  // 合併多個賽季：升降班會令單一賽季名單覆蓋唔到今季球隊
  const merged = new Map<string, Team>();
  // 免費層額度有限：最多查兩季（當季 ＋ 免費層最新嘅 2024），成果由邊緣快取七日
  for (const season of [seasonOf(), 2024]) {
    const res = await fetch(
      `https://v3.football.api-sports.io/teams?league=${league}&season=${season}`,
      { headers: { "x-apisports-key": key, "user-agent": "tianxi-site" } },
    );
    if (!res.ok) continue;
    const data = (await res.json()) as {
      response?: Array<{ team?: { name?: string; code?: string; logo?: string } }>;
    };
    for (const r of data.response ?? []) {
      const t = r.team;
      if (!t?.name || !t.logo) continue;
      if (!merged.has(t.name)) {
        merged.set(t.name, { name: t.name, short: t.name, tla: t.code ?? "", crest: t.logo });
      }
    }
  }
  return Array.from(merged.values());
}

export const Route = createFileRoute("/api/public/football-crests")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const url = new URL(request.url);
        const div = (url.searchParams.get("div") ?? "").toUpperCase();
        const code = COMP[div];
        const json = (body: unknown, status = 200, maxAge = 604800) =>
          new Response(JSON.stringify(body), {
            status,
            headers: {
              "content-type": "application/json; charset=utf-8",
              "cache-control": `public, max-age=${maxAge}, s-maxage=${maxAge}, stale-while-revalidate=2592000`,
              "access-control-allow-origin": "*",
            },
          });

        if (!div) return json({ ok: false, error: "missing div" }, 400, 60);

        const token = process.env["FOOTBALL_DATA_ORG_TOKEN"];
        // 第一順位源未覆蓋（或無 token）：改由 API-Football 補徽
        if (!code || !token) {
          try {
            const teams = await fromApiFootball(div);
            return json(
              { ok: true, div, source: teams.length ? "api-football" : null, teams },
              200,
              teams.length ? 604800 : 3600,
            );
          } catch {
            return json({ ok: true, div, source: null, teams: [] as Team[] }, 200, 300);
          }
        }


        try {
          const res = await fetch(`https://api.football-data.org/v4/competitions/${code}/teams`, {
            headers: { "X-Auth-Token": token, "user-agent": "tianxi-site" },
          });
          if (!res.ok) throw new Error(`upstream ${res.status}`);
          const data = (await res.json()) as {
            teams?: Array<{ name?: string; shortName?: string; tla?: string; crest?: string }>;
          };
          const teams: Team[] = (data.teams ?? [])
            .filter((t) => t.crest && t.name)
            .map((t) => ({
              name: t.name ?? "",
              short: t.shortName ?? "",
              tla: t.tla ?? "",
              crest: t.crest ?? "",
            }));
          if (teams.length) return json({ ok: true, div, source: "football-data.org", teams });
          throw new Error("empty");
        } catch (err) {
          // 第一順位源失敗／空：退去 API-Football，兩邊都唔得先交由前端出派生盾形標
          try {
            const fb = await fromApiFootball(div);
            if (fb.length) return json({ ok: true, div, source: "api-football", teams: fb });
          } catch {
            /* 落到下面短快取空回覆 */
          }
          return json(
            {
              ok: false,
              div,
              source: "football-data.org",
              teams: [] as Team[],
              error: err instanceof Error ? err.message : "unknown",
            },
            200,
            300,
          );
        }
      },
    },
  },
});
