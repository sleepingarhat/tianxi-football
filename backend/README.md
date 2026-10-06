# backend/ — 天喜足球服務層（真實後端程式）

呢個目錄係天喜網站實際運行嘅足球後端程式（TanStack Start server routes，跑喺 Lovable Cloud），
同網站逐字節一致，唔係淨係說明。

## 結構
- `routes/api/public/` — 公開 API 端點：雙引擎鎖定（football-dual-lock）、賽程／聯賽（football-predictions／league）、
  陣容結算（football-lineup-settle）、即時賽果（football-live*）、資料庫→GitHub 鏡像（data-mirror）、Telegram 告警（telegram-alert）等
- `lib/` — 雙引擎 dual-v1（footballDualEngine）、BSD 陣容快照（bsdLineups.server）、隊名繁中對照（teamZh）、GitHub 鏡像等
- `supabase-client/` — 資料庫連接與定時任務驗證
- `migrations/` — 資料庫結構（football_dual_ledger 鎖定帳、model_versions 版本登記、football_lineup_* 快照）

## 運行環境
程式喺 Lovable Cloud（Cloudflare Workers 運行時）運行；定時任務每 15 分鐘呼叫
`POST /api/public/football-dual-lock`（header `x-cron-secret`）完成 T−6h 鎖定、
BSD 快照同 GitHub 鏡像同步。所需環境變數：GITHUB_TOKEN、WEATHER_CRON_SECRET、BSD_API_TOKEN、
LOVABLE_API_KEY、TELEGRAM_API_KEY（告警）等。賠率權重永遠 0。
