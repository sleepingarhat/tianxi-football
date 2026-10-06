/**
 * Telegram 告警（經 connector gateway，@tianxienginebot）。
 * 涵蓋三大範圍：數據庫健康、賽果同步、雙引擎狀態。
 * 只報不修：任何告警都唔會改凍結預測、帳本或鎖定規則。
 * 去重：同一檢查項 12 小時內只發一次；恢復正常會發「已恢復」通知（24 小時內）。
 */
const GATEWAY = "https://connector-gateway.lovable.dev/telegram";
const GH = "https://api.github.com";
const OWNER = "sleepingarhat";
const WORKER_BASE = "https://tianxi-backend.tianxi-entertainment.workers.dev";
const DEDUPE_MS = 12 * 60 * 60_000;
const RECOVERY_MS = 24 * 60 * 60_000;

type Check = { key: string; area: string; fail: string | null };

async function stateClient() {
  const { supabaseAdmin } = await import("@/integrations/supabase/client.server");
  return supabaseAdmin;
}

function ghHeaders(token: string) {
  return { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}`, "User-Agent": "tianxi-web" };
}

/* ---------- Telegram 發送 ---------- */

async function tgCall(method: string, body: Record<string, unknown>) {
  const lovable = process.env["LOVABLE_API_KEY"];
  const key = process.env["TELEGRAM_API_KEY"];
  if (!lovable || !key) throw new Error("telegram gateway not configured");
  const res = await fetch(`${GATEWAY}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${lovable}`, "X-Connection-Api-Key": key },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`telegram ${method} ${res.status}: ${text.slice(0, 300)}`);
  let j: { ok?: boolean; result?: unknown; description?: string } = {};
  try { j = JSON.parse(text) as typeof j; } catch { /* gateway 回非 JSON */ }
  if (j.ok === false) throw new Error(`telegram ${method}: ${j.description ?? text.slice(0, 200)}`);
  return j.result;
}

async function resolveChatId(): Promise<number | null> {
  const envId = Number(process.env["TELEGRAM_ALERT_CHAT_ID"]);
  if (Number.isInteger(envId) && envId !== 0) return envId;
  const sb = await stateClient();
  const { data } = await sb.from("telegram_alert_state").select("value").eq("key", "chat_id").maybeSingle();
  if (data) {
    const saved = (data as { value?: { id?: number } }).value;
    if (saved?.id) return saved.id;
  }
  try {
    const result = (await tgCall("getUpdates", { limit: 10, timeout: 0 })) as
      | { message?: { chat?: { id?: number } }; channel_post?: { chat?: { id?: number } } }[]
      | null;
    const updates = result ?? [];
    for (let i = updates.length - 1; i >= 0; i--) {
      const id = updates[i]?.message?.chat?.id ?? updates[i]?.channel_post?.chat?.id;
      if (id) {
        await sb.from("telegram_alert_state").upsert({ key: "chat_id", value: { id, found_at: new Date().toISOString() } });
        return id;
      }
    }
  } catch (e) {
    console.error("telegram getUpdates failed", e);
  }
  return null;
}

export async function sendTelegramAlert(text: string): Promise<{ sent: boolean; detail: string }> {
  const chatId = await resolveChatId();
  if (!chatId) return { sent: false, detail: "no_chat_id: 用戶未同 @tianxienginebot 講過嘢" };
  try {
    await tgCall("sendMessage", { chat_id: chatId, text, disable_web_page_preview: true });
    return { sent: true, detail: "sent" };
  } catch (e) {
    return { sent: false, detail: String(e) };
  }
}

/* ---------- 檢查項 ---------- */

/** 1. 數據庫健康：核心表可讀 */
async function checkDatabase(): Promise<Check> {
  try {
    const sb = await stateClient();
    const { error } = await sb.from("football_dual_ledger").select("match_key", { count: "exact", head: true });
    return { key: "db", area: "數據庫", fail: error ? `核心表讀取失敗：${error.message}` : null };
  } catch (e) {
    return { key: "db", area: "數據庫", fail: `連線失敗：${String(e).slice(0, 200)}` };
  }
}

/** 2. 賽馬引擎健康（Worker upstream）：任何 FAIL 即告警 */
async function checkEngineHealth(): Promise<Check> {
  try {
    const res = await fetch(`${WORKER_BASE}/api/analyze/engine-health`, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) return { key: "engine-health", area: "賽果同步", fail: `引擎健康 upstream ${res.status}` };
    const j = (await res.json()) as unknown;
    const fails: string[] = [];
    const walk = (node: unknown, path: string) => {
      if (Array.isArray(node)) { node.forEach((n, i) => walk(n, `${path}[${i}]`)); return; }
      if (node && typeof node === "object") {
        const o = node as Record<string, unknown>;
        const status = typeof o["status"] === "string" ? o["status"] : null;
        if (status && /fail/i.test(status)) fails.push(`${typeof o["name"] === "string" ? o["name"] : path}`);
        for (const [k, v] of Object.entries(o)) if (k !== "status" && k !== "name") walk(v, `${path}.${k}`);
      }
    };
    walk(j, "root");
    return { key: "engine-health", area: "賽果同步", fail: fails.length ? `賽馬引擎健康檢查 FAIL：${fails.slice(0, 5).join("、")}` : null };
  } catch (e) {
    return { key: "engine-health", area: "賽果同步", fail: `引擎健康查詢失敗：${String(e).slice(0, 160)}` };
  }
}

/** 3. 收料 workflow：最後一次運行失敗或停滯超過 30 小時 */
async function checkWorkflow(token: string, repo: string, file: string, label: string, staleHours: number): Promise<Check> {
  const key = `wf:${repo}/${file}`;
  try {
    const res = await fetch(`${GH}/repos/${OWNER}/${repo}/actions/workflows/${file}/runs?per_page=1`, { headers: ghHeaders(token), signal: AbortSignal.timeout(8000) });
    if (!res.ok) return { key, area: "賽果同步", fail: null }; // 查唔到唔當故障（例如 workflow 檔改名）
    const j = (await res.json()) as { workflow_runs?: { conclusion: string | null; status: string; started_at: string }[] };
    const run = j.workflow_runs?.[0];
    if (!run) return { key, area: "賽果同步", fail: `${label}：從未運行` };
    if (run.status !== "completed") return { key, area: "賽果同步", fail: null };
    if (run.conclusion && run.conclusion !== "success" && run.conclusion !== "skipped") {
      return { key, area: "賽果同步", fail: `${label}：最後一次運行失敗（${run.conclusion}，${run.started_at}）` };
    }
    const ageH = (Date.now() - Date.parse(run.started_at)) / 3600_000;
    if (ageH > staleHours) return { key, area: "賽果同步", fail: `${label}：收料停滯 ${Math.round(ageH)} 小時無運行` };
    return { key, area: "賽果同步", fail: null };
  } catch (e) {
    return { key, area: "賽果同步", fail: `${label}檢查失敗：${String(e).slice(0, 160)}` };
  }
}

/** 4. 賽程收料：upcoming.json 超過 30 小時無更新 */
async function checkUpcomingFreshness(token: string): Promise<Check> {
  const key = "upcoming-fresh";
  try {
    const res = await fetch(`${GH}/repos/${OWNER}/tianxi-football/commits?path=data/predictions/upcoming.json&per_page=1`, { headers: ghHeaders(token), signal: AbortSignal.timeout(8000) });
    if (!res.ok) return { key, area: "雙引擎", fail: null };
    const j = (await res.json()) as { commit?: { committer?: { date?: string } } }[];
    const date = j[0]?.commit?.committer?.date;
    if (!date) return { key, area: "雙引擎", fail: null };
    const ageH = (Date.now() - Date.parse(date)) / 3600_000;
    if (ageH > 30) return { key, area: "雙引擎", fail: `賽程收料停滯：upcoming.json ${Math.round(ageH)} 小時無更新` };
    return { key, area: "雙引擎", fail: null };
  } catch (e) {
    return { key, area: "雙引擎", fail: `賽程收料檢查失敗：${String(e).slice(0, 160)}` };
  }
}

type Fixture = { match_key: string; home: string; away: string; kickoff_utc: string };

/** 5. 雙引擎：臨近開賽（≤90 分鐘）未鎖定 */
async function checkImminentLock(matches: Fixture[]): Promise<Check> {
  const key = "imminent-lock";
  try {
    const now = Date.now();
    const imminent = matches.filter((m) => {
      const t = Date.parse(m.kickoff_utc);
      return Number.isFinite(t) && t > now && t - now <= 90 * 60_000;
    });
    if (!imminent.length) return { key, area: "雙引擎", fail: null };
    const sb = await stateClient();
    const { data } = await sb.from("football_dual_ledger").select("match_key").in("match_key", imminent.map((m) => m.match_key));
    const locked = new Set((data ?? []).map((r) => (r as { match_key: string }).match_key));
    const missing = imminent.filter((m) => !locked.has(m.match_key));
    if (missing.length) {
      const names = missing.slice(0, 4).map((m) => `${m.home} vs ${m.away}`).join("、");
      return { key, area: "雙引擎", fail: `${missing.length} 場 90 分鐘內開賽仍未鎖定：${names}${missing.length > 4 ? " 等" : ""}` };
    }
    return { key, area: "雙引擎", fail: null };
  } catch (e) {
    return { key, area: "雙引擎", fail: `臨近鎖定檢查失敗：${String(e).slice(0, 160)}` };
  }
}

/** 6. BSD 陣容快照：2 小時內開賽嘅已鎖場次無快照 */
async function checkSnapshots(): Promise<Check> {
  const key = "snapshot-missing";
  try {
    const sb = await stateClient();
    const now = new Date();
    const in2h = new Date(now.getTime() + 2 * 3600_000);
    const { data: lockedRows, error } = await sb.from("football_dual_ledger").select("match_key,home,away,kickoff_utc")
      .gte("kickoff_utc", now.toISOString()).lte("kickoff_utc", in2h.toISOString()).limit(50);
    if (error) return { key, area: "雙引擎", fail: null };
    const rows = (lockedRows ?? []) as { match_key: string; home: string; away: string; kickoff_utc: string }[];
    if (!rows.length) return { key, area: "雙引擎", fail: null };
    const { data: snaps } = await sb.from("football_lineup_snapshots").select("match_key").in("match_key", rows.map((r) => r.match_key));
    const have = new Set((snaps ?? []).map((r) => (r as { match_key: string }).match_key));
    const missing = rows.filter((r) => !have.has(r.match_key));
    if (missing.length) {
      const names = missing.slice(0, 3).map((m) => `${m.home} vs ${m.away}`).join("、");
      return { key, area: "雙引擎", fail: `${missing.length} 場 2 小時內開賽仍無 BSD 陣容快照：${names}${missing.length > 3 ? " 等" : ""}` };
    }
    return { key, area: "雙引擎", fail: null };
  } catch (e) {
    return { key, area: "雙引擎", fail: null };
  }
}

/* ---------- 去重 + 發送 ---------- */

export type AlertRunResult = {
  ok: boolean;
  checked: number;
  failing: string[];
  alerted: string[];
  recovered: string[];
  sent: boolean;
  detail?: string;
};

export async function runAlertChecks(trigger = "manual", matches?: Fixture[]): Promise<AlertRunResult> {
  const out: AlertRunResult = { ok: true, checked: 0, failing: [], alerted: [], recovered: [], sent: false };
  try {
    const token = process.env["GITHUB_TOKEN"];
    const checks: Check[] = [];
    checks.push(await checkDatabase());
    checks.push(await checkEngineHealth());
    if (token) {
      checks.push(await checkWorkflow(token, "tianxi-database", "capy_race_daily.yml", "賽馬賽果收料（capy_race_daily）", 30));
      checks.push(await checkWorkflow(token, "tianxi-football", "football_daily.yml", "足球每日收料（football_daily）", 30));
      checks.push(await checkUpcomingFreshness(token));
    }
    if (matches?.length) checks.push(await checkImminentLock(matches));
    checks.push(await checkSnapshots());
    out.checked = checks.length;

    const failing = checks.filter((c) => c.fail);
    const passing = checks.filter((c) => !c.fail);
    out.failing = failing.map((c) => `${c.area}：${c.fail}`);

    const sb = await stateClient();
    const keys = checks.map((c) => `alert:${c.key}`);
    const { data: prevRows } = await sb.from("telegram_alert_state").select("key,value,updated_at").in("key", keys);
    const prev = new Map((prevRows ?? []).map((r) => [r.key as string, r as { key: string; value: { sent_at?: string; fail?: string | null }; updated_at: string }]));

    const toSend: string[] = [];
    for (const c of failing) {
      const p = prev.get(`alert:${c.key}`);
      const lastSent = p?.value?.sent_at ? Date.parse(p.value.sent_at) : 0;
      if (!p || Date.now() - lastSent >= DEDUPE_MS) {
        toSend.push(`🚨 ${c.area}：${c.fail}`);
        out.alerted.push(c.key);
      }
    }
    for (const c of passing) {
      const p = prev.get(`alert:${c.key}`);
      const lastFail = p?.value?.fail;
      if (lastFail && p?.value?.sent_at && Date.now() - Date.parse(p.value.sent_at) <= RECOVERY_MS) {
        toSend.push(`✅ 已恢復 ${c.area}（之前：${lastFail}）`);
        out.recovered.push(c.key);
      }
    }

    if (toSend.length) {
      const nowHk = new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 16).replace("T", " ");
      const msg = `天喜引擎告警（${nowHk} 香港時間，觸發：${trigger}）\n\n${toSend.join("\n")}`;
      const send = await sendTelegramAlert(msg);
      out.sent = send.sent;
      out.detail = send.detail;
    }

    // 記錄最新狀態：發送成功先記 sent_at；發送失敗保留舊記錄，下輪重發
    const nowIso = new Date().toISOString();
    const upserts = checks.map((c) => {
      const p = prev.get(`alert:${c.key}`);
      const prevSent = p?.value?.sent_at ?? null;
      if (c.fail) {
        const wasAlerted = out.alerted.includes(c.key);
        return { key: `alert:${c.key}`, value: { fail: c.fail, sent_at: wasAlerted && out.sent ? nowIso : prevSent } };
      }
      return { key: `alert:${c.key}`, value: { fail: null, sent_at: null } };
    });
    if (upserts.length) await sb.from("telegram_alert_state").upsert(upserts as never);
    return out;
  } catch (e) {
    return { ...out, ok: false, detail: String(e) };
  }
}
