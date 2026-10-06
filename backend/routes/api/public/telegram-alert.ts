import { createFileRoute } from "@tanstack/react-router";

/**
 * Telegram 告警手動／定時觸發端點（POST，x-cron-secret 保護）。
 * 檢查範圍：數據庫健康、賽果同步（賽馬＋足球收料 workflow）、雙引擎狀態（臨近未鎖、快照缺失）。
 * 只報不修：唔會改任何凍結數值、帳本或鎖定資料。
 * ?test=1 會另外發一條測試訊息，用嚟核對 Bot 通道。
 */
export const Route = createFileRoute("/api/public/telegram-alert")({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const secret = process.env["WEATHER_CRON_SECRET"];
        if (!secret || request.headers.get("x-cron-secret") !== secret) {
          return new Response("Unauthorized", { status: 401 });
        }
        const url = new URL(request.url);
        const test = url.searchParams.get("test") === "1";
        const { runAlertChecks, sendTelegramAlert } = await import("@/lib/telegramAlert.server");
        const result = await runAlertChecks("cron");
        let testResult: unknown = null;
        if (test) testResult = await sendTelegramAlert("天喜引擎告警通道測試：收到呢條訊息即代表告警已接通。");
        return Response.json({ ok: result.ok, ...result, test: testResult });
      },
    },
  },
});
