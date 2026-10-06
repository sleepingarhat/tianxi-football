/**
 * 資料庫 → GitHub 鏡像：每次有新資料寫入後，按月重新匯出整份 JSON，
 * 令網站資料庫同公開倉庫內容逐行一致（同一份資料重跑結果相同，冇重複）。
 * 失敗只記錄，唔影響原本鎖定／入帳流程。
 */
const GH = "https://api.github.com";
const OWNER = "sleepingarhat";

type Spec = { repo: string; dir: string; table: string; timeCol: string; select?: string };

export const MIRRORS = {
  dualLedger: { repo: "tianxi-football", dir: "data/ledger/dual-v1", table: "football_dual_ledger", timeCol: "kickoff_utc" },
  lineupSnapshots: { repo: "tianxi-football", dir: "data/lineups/snapshots", table: "football_lineup_snapshots", timeCol: "kickoff_utc" },
  lineupSettle: { repo: "tianxi-football", dir: "data/lineups/settle", table: "football_lineup_settle", timeCol: "settled_at" },
} satisfies Record<string, Spec>;

function headers(token: string) {
  return { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}`, "User-Agent": "tianxi-web", "Content-Type": "application/json" };
}

export async function mirrorMonths(spec: Spec, months: string[]) {
  const token = process.env["GITHUB_TOKEN"];
  if (!token) return { ok: false, error: "no token" };
  const { supabaseAdmin } = await import("@/integrations/supabase/client.server");
  const out: Record<string, unknown> = {};
  for (const ym of [...new Set(months)]) {
    try {
      const [y, m] = ym.split("-").map(Number) as [number, number];
      const start = new Date(Date.UTC(y, m - 1, 1)).toISOString();
      const end = new Date(Date.UTC(y, m, 1)).toISOString();
      const { data, error } = await supabaseAdmin.from(spec.table as never).select(spec.select ?? "*")
        .gte(spec.timeCol, start).lt(spec.timeCol, end).order(spec.timeCol, { ascending: true }).limit(5000);
      if (error) { out[ym] = error.message; continue; }
      const body = JSON.stringify({ table: spec.table, month: ym, count: data?.length ?? 0, rows: data ?? [] }, null, 1) + "\n";
      const path = `${spec.dir}/${ym}.json`;
      const url = `${GH}/repos/${OWNER}/${spec.repo}/contents/${path}`;
      const meta = await fetch(url, { headers: headers(token) });
      let sha: string | undefined;
      if (meta.ok) {
        const j = (await meta.json()) as { sha?: string; content?: string };
        sha = j.sha;
        const old = j.content ? Buffer.from(j.content, "base64").toString("utf8") : "";
        if (old === body) { out[ym] = "unchanged"; continue; }
      }
      const put = await fetch(url, {
        method: "PUT", headers: headers(token),
        body: JSON.stringify({ message: `data(${spec.table}): sync ${ym} (${data?.length ?? 0} rows)`, content: Buffer.from(body, "utf8").toString("base64"), ...(sha ? { sha } : {}) }),
      });
      out[ym] = put.ok ? data?.length ?? 0 : `put ${put.status}: ${await put.text()}`;
      if (!put.ok) console.error("github mirror failed", spec.table, ym, out[ym]);
    } catch (e) {
      out[ym] = String(e);
      console.error("github mirror error", spec.table, ym, e);
    }
  }
  return { ok: true, months: out };
}

export const monthOf = (iso: string) => iso.slice(0, 7);
