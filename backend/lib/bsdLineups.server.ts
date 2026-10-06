/** BSD（sports.bzzoiro.com）陣容快照：研究軌，唔入正式戰績，權重 0。 */
const BASE = "https://sports.bzzoiro.com/api/v2";

type Fx = { match_key: string; div: string; home: string; away: string; kickoff_utc: string };
type BsdEvent = { id: number; home_team: string; away_team: string; event_date: string; status?: string; home_score?: number | null };

const norm = (s: string) =>
  s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/\b(fc|cf|afc|ac|sc|ssc|as|us|rc|cd|ud|sd|vfb|vfl|tsg|1\.|de|club)\b/g, "").replace(/[^a-z]/g, "");
const same = (a: string, b: string) => { const x = norm(a), y = norm(b); return !!x && !!y && (x.includes(y) || y.includes(x) || x.slice(0, 5) === y.slice(0, 5)); };

async function get<T>(path: string, token: string): Promise<T | null> {
  try {
    const r = await fetch(`${BASE}${path}`, { headers: { Authorization: `Token ${token}` } });
    return r.ok ? ((await r.json()) as T) : null;
  } catch { return null; }
}

async function eventsOn(date: string, token: string) {
  const out: BsdEvent[] = [];
  for (let off = 0; off < 1000; off += 200) {
    const j = await get<{ results: BsdEvent[]; next: string | null }>(`/events/?date_from=${date}&date_to=${date}&limit=200&offset=${off}`, token);
    if (!j) break;
    out.push(...j.results);
    if (!j.next) break;
  }
  return out;
}

export async function findBsdEvents(list: Fx[], token: string) {
  const days = [...new Set(list.map((m) => m.kickoff_utc.slice(0, 10)))];
  const byDay = new Map<string, BsdEvent[]>();
  for (const d of days) byDay.set(d, await eventsOn(d, token));
  return new Map(list.map((m) => {
    const t = Date.parse(m.kickoff_utc);
    const ev = (byDay.get(m.kickoff_utc.slice(0, 10)) ?? []).find(
      (e) => Math.abs(Date.parse(e.event_date) - t) <= 20 * 60_000 && same(e.home_team, m.home) && same(e.away_team, m.away),
    );
    return [m.match_key, ev ?? null] as const;
  }));
}

export async function fetchLineups(eventId: number, token: string) {
  return get<{ lineup_status: string; lineups: unknown; unavailable_players: unknown }>(`/events/${eventId}/lineups/`, token);
}

async function predictionsOn(date: string, token: string) {
  const j = await get<{ results: { event?: { id?: number } | number; event_id?: number }[] }>(`/predictions/?date_from=${date}&date_to=${date}&limit=500`, token);
  const map = new Map<number, unknown>();
  for (const p of j?.results ?? []) {
    const id = typeof p.event === "number" ? p.event : p.event?.id ?? p.event_id;
    if (id) { const { event: _e, ...rest } = p as Record<string, unknown>; void _e; map.set(id, rest); }
  }
  return map;
}

/** 賽前情報：球證、天氣、場地、長途、打吡、中立場、對賽往績、開盤價（賠率只記帳，權重 0） */
export async function fetchContext(eventId: number, token: string) {
  const ev = await get<Record<string, unknown>>(`/events/${eventId}/`, token);
  if (!ev) return null;
  const refId = ev["referee_id"] as number | null;
  const [ref, odds] = await Promise.all([
    refId ? get<Record<string, unknown>>(`/referees/${refId}/`, token) : Promise.resolve(null),
    get<{ odds?: unknown; last_update_at?: string }>(`/events/${eventId}/odds/`, token),
  ]);
  return {
    round: ev["round_label"] ?? null, weather: ev["weather"] ?? null, pitch_condition: ev["pitch_condition"] ?? null,
    travel_km: ev["travel_distance_km"] ?? null, derby: ev["is_local_derby"] ?? null, neutral: ev["is_neutral_ground"] ?? null,
    h2h: ev["head_to_head"] ?? null, referee: ref, odds: odds?.odds ?? null, odds_at: odds?.last_update_at ?? null,
  };
}

/** 賽後：技術統計（含 xG）同事件時序；只作賽後研究，唔入預測 */
export async function fetchPost(eventId: number, token: string) {
  const [st, inc] = await Promise.all([
    get<{ stats?: unknown }>(`/events/${eventId}/stats/`, token),
    get<{ incidents?: unknown }>(`/events/${eventId}/incidents/`, token),
  ]);
  return { post_stats: st?.stats ?? null, incidents: inc?.incidents ?? null };
}

/** T−6h 建快照行（跟雙引擎鎖定線）；任何失敗都寫 error 欄，唔阻鎖定 */
export async function buildSnapshots(list: Fx[], token: string) {
  const events = await findBsdEvents(list, token);
  const preds = new Map<string, Map<number, unknown>>();
  for (const d of new Set(list.map((m) => m.kickoff_utc.slice(0, 10)))) preds.set(d, await predictionsOn(d, token));
  const rows = [];
  for (const m of list) {
    const ev = events.get(m.match_key);
    const t0 = Date.now();
    const lu = ev ? await fetchLineups(ev.id, token) : null;
    const latency = ev ? Date.now() - t0 : null;
    const context = ev ? await fetchContext(ev.id, token) : null;
    rows.push({
      match_key: m.match_key, bsd_event_id: ev?.id ?? null, kickoff_utc: m.kickoff_utc, div: m.div, home: m.home, away: m.away,
      lineup_status: lu?.lineup_status ?? null, lineups: lu?.lineups ?? null, unavailable: lu?.unavailable_players ?? null,
      bsd_prediction: ev ? preds.get(m.kickoff_utc.slice(0, 10))?.get(ev.id) ?? null : null,
      bsd_latency_ms: latency, context,
      error: !ev ? "no bsd event match" : !lu ? "lineups unavailable" : null,
    });
  }
  return rows;
}

type Side = { formation?: string; players?: { id: number }[] };
/** 完場對比：預計正選 vs 官方正選 */
export function compare(pred: { home?: Side; away?: Side } | null, off: { home?: Side; away?: Side } | null) {
  const hits = (a?: Side, b?: Side) => {
    const s = new Set((b?.players ?? []).map((p) => p.id));
    return (a?.players ?? []).filter((p) => s.has(p.id)).length;
  };
  return {
    home_hits: hits(pred?.home, off?.home), away_hits: hits(pred?.away, off?.away),
    home_formation_ok: !!pred?.home?.formation && pred.home.formation === off?.home?.formation,
    away_formation_ok: !!pred?.away?.formation && pred.away.formation === off?.away?.formation,
  };
}
