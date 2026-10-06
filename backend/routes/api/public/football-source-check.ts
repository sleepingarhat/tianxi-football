import { createFileRoute } from "@tanstack/react-router";

/**
 * 足球數據源驗收（內部用）：
 * 只在服務端讀取密鑰，向上游發最小請求，回傳狀態／配額／覆蓋範圍，
 * 永不回傳密鑰本身或任何請求原文。
 */

async function checkFootyStats(key: string) {
  // league-list 係最輕量嘅端點，用嚟驗證 key 有效同配額
  const url = `https://api.footystats.org/league-list?key=${encodeURIComponent(key)}&chosen_leagues_only=true`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 12_000);
  try {
    const res = await fetch(url, { signal: ctrl.signal, headers: { accept: "application/json" } });
    if (!res.ok) return { ok: false, http: res.status };
    const j = (await res.json()) as { success?: boolean; data?: unknown[] };
    const leagues = Array.isArray(j.data) ? j.data.length : 0;
    return { ok: j.success === true || leagues > 0, leagues };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.name : "error" };
  } finally {
    clearTimeout(timer);
  }
}

async function checkApiFootball(key: string) {
  // 用戶提供嘅係 apifootball.com（AllSportsAPI 旗下）嘅 key，
  // 正式接口係 apiv3.apifootball.com（v1 主域而家只回 500）。
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15_000);
  try {
    const url = `https://apiv3.apifootball.com/?action=get_leagues&APIkey=${encodeURIComponent(key)}`;
    const res = await fetch(url, { signal: ctrl.signal, headers: { accept: "application/json" } });
    if (!res.ok) return { ok: false, http: res.status };
    const j = (await res.json()) as unknown;
    if (!Array.isArray(j)) return { ok: false, error: "bad_response" };
    const leagues = j as { league_id?: string; league_name?: string }[];
    // 五大聯賽覆蓋檢查
    const big5 = ["Premier League", "Bundesliga", "La Liga", "Serie A", "Ligue 1"];
    const names = new Set(leagues.map((l) => l.league_name ?? ""));
    return {
      ok: leagues.length > 0,
      via: "apiv3.apifootball.com",
      leagues: leagues.length,
      big5: big5.filter((b) => names.has(b)),
    };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.name : "error" };
  } finally {
    clearTimeout(timer);
  }
}

async function checkApiSports(key: string) {
  // api-football.com（API-SPORTS）免費層：當季唔包，歷史 2022–2024 齊，
  // 定位＝傷停／陣容特徵嘅歷史回測數據源
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15_000);
  try {
    const res = await fetch("https://v3.football.api-sports.io/status", {
      signal: ctrl.signal,
      headers: { "x-apisports-key": key, accept: "application/json" },
    });
    if (!res.ok) return { ok: false, http: res.status };
    const j = (await res.json()) as {
      response?: {
        subscription?: { plan?: string; active?: boolean; end?: string };
        requests?: { current?: number; limit_day?: number };
      };
      errors?: unknown;
    };
    const r = j.response;
    const hasErrors =
      j.errors && (Array.isArray(j.errors) ? j.errors.length > 0 : Object.keys(j.errors).length > 0);
    return {
      ok: Boolean(r?.subscription?.active) && !hasErrors,
      via: "v3.football.api-sports.io",
      plan: r?.subscription?.plan ?? null,
      active: r?.subscription?.active ?? false,
      requests_today: r?.requests?.current ?? null,
      requests_limit_day: r?.requests?.limit_day ?? null,
      note: "免費層只包 2022–2024 歷史季，當季直播數據用 apifootball.com",
    };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.name : "error" };
  } finally {
    clearTimeout(timer);
  }
}

async function checkBigBallsData(key: string) {
  // bigballsdata.com：候選 xG／陣容／傷停來源，免費層 GitHub 登入 2,000 次/日。
  // 逐端點探測免費層權限，記低 http 狀態，斷定邊啲數據用得著。
  const base = "https://api.bigballsdata.com";
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 30_000);
  const headers = { accept: "application/json", authorization: `Bearer ${key}` };
  const probe = async (path: string) => {
    try {
      const r = await fetch(`${base}${path}`, { signal: ctrl.signal, headers });
      let xg: boolean | null = null;
      if (r.ok) {
        const raw = await r.text();
        xg = raw.includes('"xg"');
      }
      return { http: r.status, xg };
    } catch {
      return { http: 0, xg: null };
    }
  };
  try {
    const matchesRes = await fetch(`${base}/v1/matches?league=epl&status=finished&limit=5`, {
      signal: ctrl.signal,
      headers,
    });
    if (!matchesRes.ok) return { ok: false, http: matchesRes.status };
    const j = (await matchesRes.json()) as { data?: { id?: string }[] };
    const matches = Array.isArray(j.data) ? j.data : [];
    if (matches.length === 0) return { ok: false, error: "no_matches" };
    const first = matches[0]?.id ?? "";
    const probes: Record<string, unknown> = {
      match_detail: await probe(`/v1/matches/${encodeURIComponent(first)}`),
      statistics: await probe(`/v1/matches/${encodeURIComponent(first)}/statistics`),
      xg_leaders: await probe(`/v1/leagues/epl/xg-leaders`),
      injuries: await probe(`/v1/injuries?league=epl`),
      stored_stats: await probe(`/v1/stored/matches/${encodeURIComponent(first)}/stats`),
      stored_lineups: await probe(`/v1/stored/matches/${encodeURIComponent(first)}/lineups`),
    };
    return { ok: true, via: "api.bigballsdata.com", matches: matches.length, probes };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.name : "error" };
  } finally {
    clearTimeout(timer);
  }
}

export const Route = createFileRoute("/api/public/football-source-check")({
  server: {
    handlers: {
      GET: async () => {
        const fsKey = process.env["FOOTYSTATS_API_KEY"];
        const afKey = process.env["API_FOOTBALL_KEY"];
        const asKey = process.env["APISPORTS_API_FOOTBALL_KEY"];
        const bbKey = process.env["BIGBALLSDATA_API_KEY"];
        const out: Record<string, unknown> = { checked_at: new Date().toISOString() };
        out["footystats"] = fsKey ? await checkFootyStats(fsKey) : { ok: false, error: "missing_secret" };
        out["apifootball"] = afKey ? await checkApiFootball(afKey) : { ok: false, error: "missing_secret" };
        out["api_sports"] = asKey ? await checkApiSports(asKey) : { ok: false, error: "missing_secret" };
        out["bigballsdata"] = bbKey
          ? await checkBigBallsData(bbKey)
          : { ok: false, error: "missing_secret" };
        out["api_sports"] = asKey ? await checkApiSports(asKey) : { ok: false, error: "missing_secret" };
        return new Response(JSON.stringify(out, null, 2), {
          headers: { "content-type": "application/json; charset=utf-8" },
        });
      },
    },
  },
});
