import { createFileRoute } from "@tanstack/react-router";

/**
 * 即時賽果專用通道：football-data.org v4 → 本站（只讀 90 分鐘完場比分）。
 * 目的係上游週批 CSV 未更新時，完場一小時內就有官方比分可以結算。
 * 只取 status=FINISHED 嘅 score.fullTime（聯賽賽事本身就係 90 分鐘），
 * 加時／點球唔會攞。呢個通道唔會寫入、唔會改凍結預測、唔會升指紋。
 */
const COMP_TO_DIV: Record<string, string> = {
  PL: "E0",
  ELC: "E1",
  BL1: "D1",
  SA: "I1",
  PD: "SP1",
  FL1: "F1",
};

const json = (body: unknown, status = 200, cache?: string) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      ...(cache ? { "cache-control": cache } : {}),
    },
  });

const hkToday = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Hong_Kong" }).format(new Date());

const shift = (key: string, days: number) =>
  new Date(Date.parse(`${key}T04:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10);

export const Route = createFileRoute("/api/public/football-live-results")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const url = new URL(request.url);
        const today = hkToday();
        const dateTo = url.searchParams.get("to") ?? shift(today, 1);
        const dateFrom = url.searchParams.get("from") ?? shift(today, -7);

        const token = process.env["FOOTBALL_DATA_ORG_TOKEN"];
        if (!token) return json({ ok: false, error: "未設定即時賽果源憑證", matches: [] }, 503);

        try {
          const qs = new URLSearchParams({
            competitions: Object.keys(COMP_TO_DIV).join(","),
            dateFrom,
            dateTo,
          });
          const res = await fetch(`https://api.football-data.org/v4/matches?${qs}`, {
            headers: { "X-Auth-Token": token },
          });
          if (!res.ok) {
            const text = await res.text();
            // 上游失敗：回空清單（200），前端照用週批賽果，唔當頁面錯誤
            return json(
              { ok: false, error: `upstream ${res.status}: ${text.slice(0, 200)}`, matches: [] },
              200,
              "no-store",
            );
          }
          const data = (await res.json()) as {
            matches?: Array<{
              utcDate: string;
              status: string;
              competition?: { code?: string };
              homeTeam?: { name?: string; shortName?: string };
              awayTeam?: { name?: string; shortName?: string };
              score?: { fullTime?: { home?: number | null; away?: number | null } };
            }>;
          };

          const matches = (data.matches ?? [])
            .filter((m) => m.status === "FINISHED")
            .map((m) => {
              const div = COMP_TO_DIV[m.competition?.code ?? ""] ?? "";
              const h = m.score?.fullTime?.home;
              const a = m.score?.fullTime?.away;
              if (!div || h == null || a == null) return null;
              return {
                div,
                kickoff_utc: m.utcDate,
                home: m.homeTeam?.shortName ?? m.homeTeam?.name ?? "",
                away: m.awayTeam?.shortName ?? m.awayTeam?.name ?? "",
                ft_h: h,
                ft_a: a,
                ftr: h > a ? "home" : h === a ? "draw" : "away",
                source: "football-data.org",
              };
            })
            .filter(Boolean);

          return json(
            { ok: true, from: dateFrom, to: dateTo, count: matches.length, fetched_at: new Date().toISOString(), matches },
            200,
            // 完場後最多 10 分鐘就會見到：快取短，唔會打爆免費額度
            "public, max-age=120, s-maxage=600, stale-while-revalidate=1800",
          );
        } catch (err) {
          return json({ ok: false, error: err instanceof Error ? err.message : "unknown", matches: [] }, 200, "no-store");
        }
      },
    },
  },
});
