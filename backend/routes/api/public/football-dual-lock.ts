import { createFileRoute } from "@tanstack/react-router";
import { DUAL_ENGINE, dualEngine, OUTCOMES } from "@/lib/footballDualEngine";

/**
 * 雙引擎 T−6h 鎖定：讀凍結預測，開賽前 6 小時內（未開賽）嘅場次寫入鎖定帳。
 * 只插入（已存在就跳過），永不改寫。賠率：馬會 → Bet365 → 市場平均 → 無。
 * 2026-10-06 起鎖定線由 T−60 提前到 T−6h，新場次平注注碼 $100（舊帳保留 $10）。
 */
type Fixture = {
  match_key: string;
  div: string;
  home: string;
  away: string;
  kickoff_utc: string;
  p: [number, number, number];
  lambda: [number, number];
  status?: string;
};

const num = (v: string | number | undefined | null) => {
  const n = Number(v);
  return Number.isFinite(n) && n > 1 ? n : null;
};
type Odds = { odds: [number, number, number]; source: string };
const norm = (s: string) => s.toLowerCase().normalize("NFD").replace(/[^a-z]/g, "");

/** 後備次序：馬會 → Bet365 → Betfair 交易所 → BetVictor → Bet&Win → Betfred → Paddy Power → Sky Bet → 市場平均 → 市場最高 */
const CHAIN: [string, string][] = [
  ["B365", "b365"], ["BFE", "bfe"], ["BV", "bv"], ["BW", "bw"], ["BFD", "bfd"],
  ["PP", "pp"], ["SKB", "skb"], ["Avg", "avg"], ["Max", "max"],
];

async function hkjcOdds(): Promise<{ home: string; away: string; odds: [number, number, number] }[]> {
  try {
    const res = await fetch("https://info.cld.hkjc.com/graphql/base/", {
      method: "POST",
      headers: { "content-type": "application/json", origin: "https://bet.hkjc.com", referer: "https://bet.hkjc.com/ch/football/had" },
      body: JSON.stringify({ query: "query{matches(fbOddsTypes:[HAD]){homeTeam{name_en} awayTeam{name_en} foPools(fbOddsTypes:[HAD]){lines{combinations{str currentOdds}}}}}" }),
    });
    const j = (await res.json()) as { data?: { matches?: { homeTeam: { name_en: string }; awayTeam: { name_en: string }; foPools: { lines: { combinations: { str: string; currentOdds: string }[] }[] }[] }[] | null } };
    return (j.data?.matches ?? []).flatMap((m) => {
      const c = m.foPools?.[0]?.lines?.[0]?.combinations ?? [];
      const g = (k: string) => num(c.find((x) => x.str === k)?.currentOdds);
      const o = [g("H"), g("D"), g("A")];
      return o.every(Boolean) ? [{ home: norm(m.homeTeam.name_en), away: norm(m.awayTeam.name_en), odds: o as [number, number, number] }] : [];
    });
  } catch {
    return []; // 馬會接口要白名單，失敗即降級
  }
}

async function publicOdds() {
  const map = new Map<string, Odds>();
  try {
    const res = await fetch("https://football-data.co.uk/fixtures.csv", { redirect: "follow" });
    if (!res.ok) return map;
    const lines = (await res.text()).replace(/^\uFEFF/, "").split(/\r?\n/);
    const head = (lines[0] ?? "").split(",");
    const col = (row: string[], name: string) => row[head.indexOf(name)];
    for (const line of lines.slice(1)) {
      const r = line.split(",");
      if (r.length < 5) continue;
      const key = `${col(r, "Div")}|${col(r, "HomeTeam")}|${col(r, "AwayTeam")}`;
      for (const [p, source] of CHAIN) {
        const o = [num(col(r, `${p}H`)), num(col(r, `${p}D`)), num(col(r, `${p}A`))];
        if (o.every(Boolean)) { map.set(key, { odds: o as [number, number, number], source }); break; }
      }
    }
  } catch (e) {
    console.error("odds fetch failed", e);
  }
  return map;
}

export const Route = createFileRoute("/api/public/football-dual-lock")({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const secret = process.env["WEATHER_CRON_SECRET"];
        if (!secret || request.headers.get("x-cron-secret") !== secret) {
          return new Response("Unauthorized", { status: 401 });
        }
        // 告警檢查同鎖定並行跑，回應前先等佢完成（失敗唔影響鎖定）
        const alertRun = import("@/lib/telegramAlert.server")
          .then((m) => m.runAlertChecks("dual-lock"))
          .catch((e) => ({ ok: false, error: String(e) }));
        const core = await lockCore();
        try {
          await alertRun;
        } catch { /* 已在上面的 catch 兜底 */ }
        return core;
      },
    },
  },
});

async function lockCore(): Promise<Response> {
  const token = process.env["GITHUB_TOKEN"];
        if (!token) return Response.json({ ok: false, error: "no token" }, { status: 503 });
        const res = await fetch(
          "https://api.github.com/repos/sleepingarhat/tianxi-football/contents/data/predictions/upcoming.json",
          { headers: { Accept: "application/vnd.github.raw+json", Authorization: `Bearer ${token}`, "User-Agent": "tianxi-web" } },
        );
        if (!res.ok) return Response.json({ ok: false, error: `upstream ${res.status}` }, { status: 502 });
        const data = (await res.json()) as { matches?: Fixture[] };
        const list = data.matches ?? [];
        const now = Date.now();
        const due = list.filter((m) => {
          const t = Date.parse(m.kickoff_utc);
          return (
            Number.isFinite(t) && t > now && t - now <= DUAL_ENGINE.lockHours * 60 * 60_000 && t >= Date.parse(DUAL_ENGINE.frozenAt) &&
            Array.isArray(m.p) && Array.isArray(m.lambda) && !/cancel|postpon|susp/i.test(m.status ?? "")
          );
        });
        if (!due.length) return Response.json({ ok: true, locked: 0 });
        const [odds, hk] = await Promise.all([publicOdds(), hkjcOdds()]);
        const find = (m: Fixture): Odds | undefined => {
          const h = norm(m.home), a = norm(m.away);
          const k = hk.find((x) => (x.home.includes(h) || h.includes(x.home)) && (x.away.includes(a) || a.includes(x.away)));
          return k ? { odds: k.odds, source: "hkjc" } : odds.get(`${m.div}|${m.home}|${m.away}`);
        };
        // 冇賠率：距開賽仲有 >20 分鐘就留待下一輪（每 15 分鐘）再搵，唔即刻鎖「無賠率」
        const ready = due.filter((m) => find(m) || Date.parse(m.kickoff_utc) - now <= 20 * 60_000);
        if (!ready.length) return Response.json({ ok: true, due: due.length, locked: 0, waiting_odds: due.length });
        const { supabaseAdmin } = await import("@/integrations/supabase/client.server");
        const rows = ready.map((m) => {
          const r = dualEngine(m.p, m.lambda);
          const o = find(m);
          return {
            match_key: m.match_key,
            version: DUAL_ENGINE.version,
            div: m.div,
            home: m.home,
            away: m.away,
            kickoff_utc: m.kickoff_utc,
            p_a: m.p,
            lambda: m.lambda,
            p_b_d: r.pBDraw,
            p_final: r.pFinal,
            prediction: r.pick,
            odds: o?.odds ?? null,
            odds_source: o?.source ?? "none",
            pick_odds: o ? o.odds[OUTCOMES.indexOf(r.pick)] ?? null : null,
            stake: m.kickoff_utc >= DUAL_ENGINE.stakeV2From ? DUAL_ENGINE.stakeV2 : DUAL_ENGINE.stake,
          };
        });
        const { error, count } = await supabaseAdmin
          .from("football_dual_ledger")
          .upsert(rows, { onConflict: "match_key", ignoreDuplicates: true, count: "exact" });
        if (error) return Response.json({ ok: false, error: error.message }, { status: 500 });
        // 研究軌：BSD 陣容快照，失敗唔影響鎖定
        let snapshots = 0;
        const bsd = process.env["BSD_API_TOKEN"];
        if (bsd) {
          try {
            const { buildSnapshots } = await import("@/lib/bsdLineups.server");
            const snap = await buildSnapshots(ready, bsd);
            const r = await supabaseAdmin.from("football_lineup_snapshots")
              .upsert(snap as never, { onConflict: "match_key", ignoreDuplicates: true, count: "exact" });
            snapshots = r.count ?? 0;
          } catch (e) { console.error("bsd snapshot failed", e); }
        }
        // 即時同步公開倉庫（失敗唔影響鎖定）
        let mirror: unknown = null;
        if ((count ?? 0) > 0 || snapshots > 0) {
          const { MIRRORS, mirrorMonths, monthOf } = await import("@/lib/githubMirror.server");
          const months = ready.map((m) => monthOf(m.kickoff_utc));
          mirror = {
            ledger: (count ?? 0) > 0 ? await mirrorMonths(MIRRORS.dualLedger, months) : null,
            snapshots: snapshots > 0 ? await mirrorMonths(MIRRORS.lineupSnapshots, months) : null,
          };
        }
        return Response.json({ ok: true, due: due.length, locked: count ?? 0, snapshots, mirror });
}

