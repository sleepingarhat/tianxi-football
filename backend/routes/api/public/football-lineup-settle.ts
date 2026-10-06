import { createFileRoute } from "@tanstack/react-router";

/** 完場 2 小時後攞官方正選，對比 T−60 預計陣容；只插入。 */
export const Route = createFileRoute("/api/public/football-lineup-settle")({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const secret = process.env["WEATHER_CRON_SECRET"];
        if (!secret || request.headers.get("x-cron-secret") !== secret) return new Response("Unauthorized", { status: 401 });
        const token = process.env["BSD_API_TOKEN"];
        if (!token) return Response.json({ ok: false, error: "no token" }, { status: 503 });
        const { supabaseAdmin } = await import("@/integrations/supabase/client.server");
        const { fetchLineups, compare, fetchPost } = await import("@/lib/bsdLineups.server");
        const cutoff = new Date(Date.now() - 2 * 3600_000).toISOString();
        const since = new Date(Date.now() - 7 * 86400_000).toISOString();
        const { data: snaps } = await supabaseAdmin.from("football_lineup_snapshots")
          .select("match_key,bsd_event_id,lineups").lt("kickoff_utc", cutoff).gt("kickoff_utc", since).not("bsd_event_id", "is", null);
        const { data: done } = await supabaseAdmin.from("football_lineup_settle").select("match_key");
        const seen = new Set((done ?? []).map((d) => d.match_key));
        const todo = (snaps ?? []).filter((s) => !seen.has(s.match_key)).slice(0, 40);
        const rows = [];
        for (const s of todo) {
          const off = await fetchLineups(Number(s.bsd_event_id), token);
          if (!off || off.lineup_status === "predicted") continue;
          const post = await fetchPost(Number(s.bsd_event_id), token);
          rows.push({ match_key: s.match_key, official: off.lineups as never, post_stats: post.post_stats as never, incidents: post.incidents as never, ...compare(s.lineups as never, off.lineups as never) });
        }
        if (rows.length) {
          const { error } = await supabaseAdmin.from("football_lineup_settle").upsert(rows, { onConflict: "match_key", ignoreDuplicates: true });
          if (error) return Response.json({ ok: false, error: error.message }, { status: 500 });
        }
        let mirror: unknown = null;
        if (rows.length) {
          const { MIRRORS, mirrorMonths, monthOf } = await import("@/lib/githubMirror.server");
          mirror = await mirrorMonths(MIRRORS.lineupSettle, [monthOf(new Date().toISOString())]);
        }
        return Response.json({ ok: true, checked: todo.length, settled: rows.length, mirror });
      },
    },
  },
});
