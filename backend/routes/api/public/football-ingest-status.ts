import { createFileRoute } from "@tanstack/react-router";

/**
 * 足球收料狀態（唯讀）：讀 tianxi-football 嘅 GitHub Actions 紀錄同 data/_status/*.json。
 * 唔寫任何嘢、唔觸發任何排程。重試由排程本身負責（步驟內 3 次＋補跑時段）。
 */
const REPO = "sleepingarhat/tianxi-football";
const JOBS = [
  { key: "xg_pitchapi", workflow: "xg_pitchapi_daily.yml", label: "PitchAPI 每日 xG", cadence: "每日 13:07（補跑 17:07）" },
  { key: "xg_understat", workflow: "xg_weekly.yml", label: "Understat 每週補料（後備）", cadence: "逢星期二 12:17（星期三補跑）" },
  { key: "prematch_goal", workflow: "prematch_goal_snapshot.yml", label: "賽前傷停／陣容快照", cadence: "每 2 小時" },
];
const TTL = 120_000;
let cache: { at: number; body: unknown } | null = null;

async function gh(path: string, token: string | undefined) {
  const r = await fetch(`https://api.github.com${path}`, {
    headers: {
      Accept: "application/vnd.github+json",
      "User-Agent": "tianxi-status",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!r.ok) throw new Error(`GitHub ${r.status}`);
  return r.json();
}

export const Route = createFileRoute("/api/public/football-ingest-status")({
  server: {
    handlers: {
      GET: async () => {
        if (cache && Date.now() - cache.at < TTL) return Response.json(cache.body);
        const token = process.env["GITHUB_TOKEN"];
        const jobs = await Promise.all(
          JOBS.map(async (j) => {
            let runs: any[] = [];
            let status: any = null;
            try {
              const d = await gh(`/repos/${REPO}/actions/workflows/${j.workflow}/runs?per_page=10`, token);
              runs = (d?.workflow_runs ?? []).map((r: any) => ({
                id: r.id,
                at: r.run_started_at ?? r.created_at,
                event: r.event,
                status: r.status,
                conclusion: r.conclusion,
                attempt: r.run_attempt,
              }));
            } catch {
              runs = [];
            }
            try {
              const f = await gh(`/repos/${REPO}/contents/data/_status/${j.key}.json`, token);
              status = JSON.parse(atob(String(f.content).replace(/\n/g, "")));
            } catch {
              status = null;
            }
            return { ...j, runs, status };
          }),
        );
        const body = { generated_at: new Date().toISOString(), jobs };
        cache = { at: Date.now(), body };
        return Response.json(body);
      },
    },
  },
});
