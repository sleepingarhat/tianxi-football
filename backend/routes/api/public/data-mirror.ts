import { createFileRoute } from "@tanstack/react-router";

/** 手動／補跑：把指定月份嘅足球帳本同陣容資料重新同步到公開倉庫。 */
export const Route = createFileRoute("/api/public/data-mirror")({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const secret = process.env["WEATHER_CRON_SECRET"];
        if (!secret || request.headers.get("x-cron-secret") !== secret) return new Response("Unauthorized", { status: 401 });
        const url = new URL(request.url);
        const months = (url.searchParams.get("months") ?? new Date().toISOString().slice(0, 7))
          .split(",").filter((m) => /^\d{4}-\d{2}$/.test(m)).slice(0, 12);
        const { MIRRORS, mirrorMonths } = await import("@/lib/githubMirror.server");
        const result: Record<string, unknown> = {};
        for (const [k, spec] of Object.entries(MIRRORS)) result[k] = await mirrorMonths(spec, months);
        return Response.json({ ok: true, months, result });
      },
    },
  },
});
