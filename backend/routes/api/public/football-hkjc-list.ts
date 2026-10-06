import { createFileRoute } from "@tanstack/react-router";

/**
 * 香港賽馬會即場足球主和客有盤場次清單（只讀、公開）。
 * 用途：前端「只顯示馬會有盤」篩選；賠率數值唔入模，呢度連盤名都唔回傳，淨回隊名對。
 * 馬會接口要白名單，失敗即回空陣列，前端篩選自動失效。
 */

const norm = (s: string) => s.toLowerCase().normalize("NFD").replace(/[^a-z]/g, "");

async function hkjcList(): Promise<{ home: string; away: string }[]> {
  try {
    const res = await fetch("https://info.cld.hkjc.com/graphql/base/", {
      method: "POST",
      headers: { "content-type": "application/json", origin: "https://bet.hkjc.com", referer: "https://bet.hkjc.com/ch/football/had" },
      body: JSON.stringify({ query: "query{matches(fbOddsTypes:[HAD]){homeTeam{name_en} awayTeam{name_en} foPools(fbOddsTypes:[HAD]){lines{combinations{str currentOdds}}}}}" }),
    });
    const j = (await res.json()) as { data?: { matches?: { homeTeam: { name_en: string }; awayTeam: { name_en: string }; foPools: { lines: { combinations: { str: string; currentOdds: string }[] }[] }[] }[] | null } };
    return (j.data?.matches ?? []).flatMap((m) => {
      const c = m.foPools?.[0]?.lines?.[0]?.combinations ?? [];
      const hasAll = ["H", "D", "A"].every((k) => {
        const n = Number(c.find((x) => x.str === k)?.currentOdds);
        return Number.isFinite(n) && n > 1;
      });
      return hasAll ? [{ home: norm(m.homeTeam.name_en), away: norm(m.awayTeam.name_en) }] : [];
    });
  } catch {
    return [];
  }
}

export const Route = createFileRoute("/api/public/football-hkjc-list")({
  server: {
    handlers: {
      GET: async () => {
        const pairs = await hkjcList();
        return Response.json(
          { ok: true, count: pairs.length, pairs },
          { headers: { "cache-control": "public, max-age=600" } },
        );
      },
    },
  },
});
