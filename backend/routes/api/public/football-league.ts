import { createFileRoute } from "@tanstack/react-router";

/**
 * 聯賽層公開讀取：積分榜、天喜足球ELO（自建、賽前 as-of）、主客攻防分拆、逐隊近況。
 * 全部由 tianxi-football 已落地嘅賽果 CSV 即場派生，唔動凍結預測軌，
 * 唔涉賠率欄（CSV 內賠率列一律唔讀）。
 */
const REPO = "sleepingarhat/tianxi-football";
const RESULT = (season: number, div: string) => `data/results/${season}_${div}.csv`;

/** 只做五大聯賽（同公開對帳同一口徑） */
export const LEAGUES: { div: string; zh: string }[] = [
  { div: "E0", zh: "英超" },
  { div: "D1", zh: "德甲" },
  { div: "SP1", zh: "西甲" },
  { div: "I1", zh: "意甲" },
  { div: "F1", zh: "法甲" },
];

const CURRENT_SEASON = 2026;
const ELO_SEASONS = [2023, 2024, 2025, 2026];
const MEAN = 1500;
const HFA = 60;
const K = 20;
const REGRESS = 0.25;

type Row = {
  date: string; // ISO yyyy-mm-dd
  home: string;
  away: string;
  hg: number;
  ag: number;
};

function parseDate(s: string): string {
  const m = s.trim().match(/^(\d{2})\/(\d{2})\/(\d{2,4})$/);
  if (!m) return "";
  const [, d, mo, y] = m;
  const yy = (y ?? "").length === 2 ? `20${y}` : y;
  return `${yy}-${mo}-${d}`;
}

function parseCsv(text: string): Row[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  const head = (lines.shift() ?? "").split(",");
  const idx = (k: string) => head.indexOf(k);
  const iD = idx("Date");
  const iH = idx("HomeTeam");
  const iA = idx("AwayTeam");
  const iHG = idx("FTHG");
  const iAG = idx("FTAG");
  const out: Row[] = [];
  for (const line of lines) {
    const c = line.split(",");
    const hg = Number(c[iHG]);
    const ag = Number(c[iAG]);
    const home = (c[iH] ?? "").trim();
    const away = (c[iA] ?? "").trim();
    const date = parseDate(c[iD] ?? "");
    if (!home || !away || !date || !Number.isFinite(hg) || !Number.isFinite(ag)) continue;
    out.push({ date, home, away, hg, ag });
  }
  out.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  return out;
}

async function fetchCsv(season: number, div: string, token: string): Promise<Row[]> {
  const res = await fetch(`https://api.github.com/repos/${REPO}/contents/${RESULT(season, div)}`, {
    headers: {
      Accept: "application/vnd.github.raw+json",
      Authorization: `Bearer ${token}`,
      "User-Agent": "tianxi-web",
    },
  });
  if (!res.ok) return [];
  return parseCsv(await res.text());
}

type TeamRow = {
  team: string;
  played: number;
  win: number;
  draw: number;
  loss: number;
  gf: number;
  ga: number;
  pts: number;
  elo: number;
  eloHome: number;
  eloAway: number;
  form: ("W" | "D" | "L")[];
  home: { played: number; gf: number; ga: number; pts: number };
  away: { played: number; gf: number; ga: number; pts: number };
  eloTrend: { date: string; elo: number }[];
  matches: {
    date: string;
    opp: string;
    venue: "H" | "A";
    gf: number;
    ga: number;
    res: "W" | "D" | "L";
    eloBefore: number;
    oppEloBefore: number;
  }[];
};

export const Route = createFileRoute("/api/public/football-league")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const url = new URL(request.url);
        const div = url.searchParams.get("div") ?? "E0";
        const league = LEAGUES.find((l) => l.div === div);
        const json = (body: unknown, status = 200, cache?: string) =>
          new Response(JSON.stringify(body), {
            status,
            headers: {
              "content-type": "application/json; charset=utf-8",
              "access-control-allow-origin": "*",
              ...(cache ? { "cache-control": cache } : {}),
            },
          });

        if (!league) return json({ ok: false, error: "unsupported league" }, 400);
        const token = process.env["GITHUB_TOKEN"];
        if (!token) return json({ ok: false, error: "未設定 GitHub token" }, 503);

        try {
          const seasons = await Promise.all(ELO_SEASONS.map((s) => fetchCsv(s, div, token)));
          const elo = new Map<string, number>();
          const table = new Map<string, TeamRow>();
          const get = (team: string): TeamRow => {
            let t = table.get(team);
            if (!t) {
              t = {
                team,
                played: 0,
                win: 0,
                draw: 0,
                loss: 0,
                gf: 0,
                ga: 0,
                pts: 0,
                elo: MEAN,
                eloHome: MEAN,
                eloAway: MEAN,
                form: [],
                home: { played: 0, gf: 0, ga: 0, pts: 0 },
                away: { played: 0, gf: 0, ga: 0, pts: 0 },
                eloTrend: [],
                matches: [],
              };
              table.set(team, t);
            }
            return t;
          };

          seasons.forEach((rows, si) => {
            const season = ELO_SEASONS[si] ?? CURRENT_SEASON;
            if (si > 0) {
              // 跨季回歸：向 1500 收 25%
              for (const [k, v] of elo) elo.set(k, MEAN + (v - MEAN) * (1 - REGRESS));
            }
            for (const r of rows) {
              const eh = elo.get(r.home) ?? MEAN;
              const ea = elo.get(r.away) ?? MEAN;
              const exp = 1 / (1 + 10 ** ((ea - (eh + HFA)) / 400));
              const s = r.hg > r.ag ? 1 : r.hg === r.ag ? 0.5 : 0;
              elo.set(r.home, eh + K * (s - exp));
              elo.set(r.away, ea + K * (exp - s));

              if (season !== CURRENT_SEASON) continue;
              const H = get(r.home);
              const A = get(r.away);
              const hres: "W" | "D" | "L" = r.hg > r.ag ? "W" : r.hg === r.ag ? "D" : "L";
              const ares: "W" | "D" | "L" = hres === "W" ? "L" : hres === "L" ? "W" : "D";
              const pt = (x: "W" | "D" | "L") => (x === "W" ? 3 : x === "D" ? 1 : 0);

              H.played += 1; H.gf += r.hg; H.ga += r.ag; H.pts += pt(hres);
              H.home.played += 1; H.home.gf += r.hg; H.home.ga += r.ag; H.home.pts += pt(hres);
              A.played += 1; A.gf += r.ag; A.ga += r.hg; A.pts += pt(ares);
              A.away.played += 1; A.away.gf += r.ag; A.away.ga += r.hg; A.away.pts += pt(ares);
              if (hres === "W") { H.win += 1; A.loss += 1; }
              else if (hres === "D") { H.draw += 1; A.draw += 1; }
              else { H.loss += 1; A.win += 1; }
              H.form.push(hres);
              A.form.push(ares);
              H.matches.push({ date: r.date, opp: r.away, venue: "H", gf: r.hg, ga: r.ag, res: hres, eloBefore: Math.round(eh), oppEloBefore: Math.round(ea) });
              A.matches.push({ date: r.date, opp: r.home, venue: "A", gf: r.ag, ga: r.hg, res: ares, eloBefore: Math.round(ea), oppEloBefore: Math.round(eh) });
              H.eloTrend.push({ date: r.date, elo: Math.round(elo.get(r.home) ?? MEAN) });
              A.eloTrend.push({ date: r.date, elo: Math.round(elo.get(r.away) ?? MEAN) });
            }
          });

          const rows = Array.from(table.values()).map((t) => ({
            ...t,
            elo: Math.round(elo.get(t.team) ?? MEAN),
            eloHome: Math.round((elo.get(t.team) ?? MEAN) + HFA),
            eloAway: Math.round(elo.get(t.team) ?? MEAN),
            form: t.form.slice(-5),
            eloTrend: t.eloTrend.slice(-12),
            matches: t.matches.slice(-12).reverse(),
          }));
          rows.sort((a, b) => b.pts - a.pts || b.gf - b.ga - (a.gf - a.ga) || b.gf - a.gf || (a.team < b.team ? -1 : 1));

          return json(
            {
              ok: true,
              div,
              league_zh: league.zh,
              season: CURRENT_SEASON,
              elo_note: "天喜足球ELO：自建、賽前 as-of、跨季回歸 25%、主場 +60，唔係 FIFA 排名",
              table: rows,
            },
            200,
            "public, max-age=300, s-maxage=1800, stale-while-revalidate=86400",
          );
        } catch (err) {
          return json({ ok: false, error: err instanceof Error ? err.message : "unknown" }, 502);
        }
      },
    },
  },
});
