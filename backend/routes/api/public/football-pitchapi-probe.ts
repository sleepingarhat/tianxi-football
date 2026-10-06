import { createFileRoute } from '@tanstack/react-router'

// 臨時探測端點：驗證 PitchAPI key 有效性同五大聯賽 xG 覆蓋。
// 只回傳彙總結果，絕不回傳 key 或完整原始數據。

const BASE = 'https://api.pitchapi.dev/v1'
const TOP5 = ['Premier League', 'La Liga', 'Serie A', 'Bundesliga', 'Ligue 1']
const TOP5_CC: Record<string, string> = { ENG: 'Premier League', ESP: 'La Liga', ITA: 'Serie A', GER: 'Bundesliga', FRA: 'Ligue 1' }

async function get(path: string, key: string) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'X-API-KEY': key },
    signal: AbortSignal.timeout(15000),
  })
  const text = await res.text()
  let body: unknown = null
  try { body = JSON.parse(text) } catch { /* non-JSON */ }
  // PitchAPI 回應包咗層 { data: ... }
  const unwrapped = (body as { data?: unknown })?.data ?? body
  return { status: res.status, body: unwrapped }
}

export const Route = createFileRoute('/api/public/football-pitchapi-probe')({
  server: {
    handlers: {
      GET: async () => {
        const key = process.env['PITCHAPI_KEY']
        if (!key) {
          return Response.json({ ok: false, reason: 'no_key' })
        }
        const report: any = { ok: true, checks: {} }
        const checks = report.checks as any

        // 1) 聯賽目錄
        const leagues = await get('/leagues', key)
        checks.leagues_status = leagues.status
        const leagueList = Array.isArray(leagues.body)
          ? leagues.body
          : (leagues.body as { leagues?: unknown[] })?.leagues ?? []
        checks.league_count = leagueList.length
        const ccFound = new Set(
          (leagueList as Array<{ country_code?: string }>).map((l) => l.country_code ?? ''),
        )
        checks.top5_found = Object.keys(TOP5_CC).filter((cc) => ccFound.has(cc))
        checks.top5_league_names = (leagueList as Array<{ name?: string; country_code?: string }>)
          .filter((l) => (l.country_code ?? '') in TOP5_CC)
          .map((l) => `${l.country_code}:${l.name}`)

        // 2) 逐日搵一場五大聯賽完場賽事（球隊係 object，league.name 先準）
        const ccByLeagueId = new Map(
          (leagueList as Array<{ id?: string; country_code?: string }>).map((l) => [l.id, l.country_code ?? '']),
        )
        const leagueName = (m: any) =>
          String((m.league as { name?: string })?.name ?? m.competition ?? '')
        const leagueCC = (m: any) =>
          ccByLeagueId.get(String((m.league as { id?: string })?.id ?? '')) ?? ''
        let sample: any
        for (const d of ['2026-09-26', '2026-09-25', '2026-09-24', '2026-09-23', '2026-09-22', '2026-09-21', '2026-09-20', '2026-09-19']) {
          const r = await get(`/date/${d}`, key)
          const arr = Array.isArray(r.body)
            ? r.body
            : (r.body as { matches?: unknown[] })?.matches ?? []
          const byLeague: Record<string, number> = {}
          for (const m of arr as any[]) {
            const cc = leagueCC(m)
            const nm = leagueName(m)
            const k = `${cc}:${nm}(${m.status ?? '?'})`
            byLeague[k] = (byLeague[k] ?? 0) + 1
          }
          checks[`date_${d}`] = { status: r.status, count: arr.length, by_league: byLeague }
          if (arr.length > 0 && !checks.match_shape) {
            const m0 = arr[0] as any
            checks.match_shape = { keys: Object.keys(m0), league: m0.league ?? null }
          }
          const hit = (arr as any[]).find((m) =>
            leagueCC(m) in TOP5_CC,
          )
          if (hit && !sample) {
            sample = hit
            checks.date_used = d
          }
        }

        if (sample) {
          const mid = String(sample.id ?? sample.match_id ?? '')
          const teamName = (t: unknown) =>
            typeof t === 'object' && t !== null ? String((t as { name?: string }).name ?? '?') : String(t ?? '?')
          checks.sample_match = {
            id: mid,
            label: `${teamName(sample.home_team ?? sample.home)} vs ${teamName(sample.away_team ?? sample.away)}`,
            league: leagueName(sample),
            status: sample.status ?? null,
          }
          const shots = await get(`/matches/${mid}/shots`, key)
          checks.shots_status = shots.status
          checks.shots_raw = JSON.stringify(shots.body ?? null)?.slice(0, 400)
          const periods = (shots.body as { periods?: Array<{ shots?: unknown[] }> })?.periods ?? []
          const shotArr = periods.flatMap((p) => p.shots ?? [])
          checks.shot_count = shotArr.length
          const firstShot = shotArr[0] as any
          checks.shot_has_xg = !!firstShot && 'expected_goals' in firstShot
          checks.shot_has_xgot = !!firstShot && 'expected_goals_on_target' in firstShot
          checks.shot_fields = firstShot ? Object.keys(firstShot).slice(0, 20) : []

          const adv = await get(`/matches/${mid}/advanced`, key)
          checks.advanced_status = adv.status
          checks.advanced_raw = JSON.stringify(adv.body ?? null)?.slice(0, 400)
          checks.advanced_has_xg = JSON.stringify(adv.body ?? {}).includes('expected_goals')
        } else {
          checks.sample_match = null
        }

        return Response.json(report)
      },
    },
  },
})
