# 條件層資料表結構（S29，只落庫、唔生成 δ）

四張只附加（append-only）JSONL 表，落 `data/context/`。
每行一條紀錄，**永不 UPDATE、永不刪行**；改正靠寫新一行（`supersedes` 指向舊 `record_id`）。

## 硬規則

1. 唔准寫 `data/predictions/`、`models/`、`snapshots/`，唔准帶任何凍結欄
   （`p`、`lambda`、`cs`、`fingerprint`、`locked_at`、`result`）。
2. 本層**只記事實**：時間戳、來源、完整度。任何 δ 估計唔喺呢層做。
3. `eligible` 由時間戳計出（見 `lineup-source-survey.md` 遲到規則），
   `eligible=false` 一律 Δλ=0、場次標紅燈（可查可睇、唔入戰績、唔入對帳分母）。
4. 缺資料唔准用平均／上仗值頂替。

## 共用欄位

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `record_id` | string | UUIDv4 |
| `match_key` | string\|null | 對應凍結帳 match_key（球員層可為 null） |
| `div` | string\|null | E0/D1/SP1/I1/F1 |
| `kickoff_utc` | string\|null | ISO8601 |
| `written_at` | string | 寫入時間（唔等於資料時間） |
| `source` | string | 授權來源標識 |
| `source_ts` | string\|null | 上游資料時間戳；缺 = null |
| `status` | string | `ok`/`late`/`post_kickoff`/`incomplete`/`missing`/`unmatched`/`stale` |
| `eligible` | bool | 是否夠資格將來入 δ；由 lead_minutes + 完整度計出 |
| `supersedes` | string\|null | 修正時指向舊 record_id |
| `notes` | string\|null | 備註 |

## lineups — 公布名單時間戳

`lineup_published_ts`、`lineup_observed_ts`、`effective_ts`、`lead_minutes`、
`starters_h`、`starters_a`（人數，齊 = 11）、`complete_h`、`complete_a`、
`team_h`、`team_a`、`source_kind`（`official`/`aggregator`）。

`effective_ts = lineup_published_ts ?? lineup_observed_ts`；
`lead_minutes = (kickoff_utc − effective_ts)/60`；`eligible = lead_minutes ≥ 60 且兩隊齊 11 人`。

## player_minutes — 穩定分鐘

`player_id`、`player_name`、`team`、`window_matches`、`minutes_total`、
`minutes_share`（0–1）、`window_end`（窗口截止賽期，必須早過 kickoff）。
`eligible = window_end < kickoff_utc 且 window_matches ≥ 5`。

## projected_xg — 賽前 projected xG

`xg_h`、`xg_a`、`is_pre_match`（必須 true）、`model_tag`。
`eligible = is_pre_match 且 source_ts ≤ kickoff − 60min`。完場 xG 一律 `eligible=false`。

## gk_saves — 門將撲救

`player_id`、`player_name`、`team`、`window_matches`、`shots_faced`、`saves`、
`save_rate`、`window_end`。`eligible` 同 `player_minutes` 規則。

## 檔案

- `data/context/lineups.jsonl`
- `data/context/player_minutes.jsonl`
- `data/context/projected_xg.jsonl`
- `data/context/gk_saves.jsonl`
- `data/context/_schema_version` — 目前 `1`（結構版本，唔係模型指紋）

## 球員資料層（S35，schema_version 2）

再加兩張只附加表，同上面四張同一套硬規則（只 append、缺資料＝missing、
唔生成 δ、唔准觸碰 `data/predictions/`、`models/`、`snapshots/`）。

### `players.jsonl` — 球員檔案＋相片連結
欄位：`player_id`、`player_name`、`player_name_zh`、`team`、`team_id`、
`shirt_number`、`position`、`nationality`、`birthdate`、`age`、`height_cm`、
`weight_kg`、`photo_url`、`photo_license`、`as_of`（＋共用欄）。

- 來源：API-SPORTS `/players/squads`（免費層當季名單可讀）。
- `photo_url` **只存連結**，唔下載、唔重新託管。`photo_license` 記明條款狀態；
  預設 `api-sports:media-link-unverified` ＝授權未確認，**唔准上前台展示**。
  要公開展示，先逐源確認（API-SPORTS 條款／Wikimedia Commons／官方媒體授權）。
- 配額：免費層 100 請求／日，每隊一個請求 → 每日只跑滾動一批（建議 ≤20 隊），
  五大 96 隊約五日跑完一輪；抓唔到就寫 missing，唔用舊值假裝新鮮。

### `injuries.jsonl` — 傷停
欄位：`player_id`、`player_name`、`team`、`team_id`、`reason`、`injury_type`、
`expected_return`、`as_of`、`season`（＋共用欄）。

- 來源：API-SPORTS `/injuries`，免費層**只包 2022–2024 季**。
- 歷史季：入庫做將來 δ_名單 嘅回測材料。
- 當季：上游一律回 plan 錯誤 → 只寫 `status=missing` 佔位行，
  **唔准用上季／平均／上仗值頂替**（等於偷近況）。
- 合格閘：`as_of` 要早過開賽前 60 分鐘（鎖定線），否則 `status=late`、`eligible=false`。

### 當季名單時間戳
API-SPORTS 免費層唔包當季，`ingest_lineups.py --source current` 改用
apifootball.com `get_events` 嘅 `lineup` 物件。上游一律**冇公布時間戳**，
所以只記 `lineup_observed_ts`＝我哋首次見到先發齊 11 人嘅時間（保守上界）；
未公布寫 missing 佔位並繼續輪詢。無時間戳一律 Δλ=0。
