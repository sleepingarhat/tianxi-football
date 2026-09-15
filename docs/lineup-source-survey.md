# 公布名單時間戳源調查（S29 資料層前置）

目的：為 `delta_squad` 找出**最早可信**嘅「公布先發名單」時間戳。
時間戳係 δ_squad 唯一合法開關——**冇時間戳 = Δλ 0**，避免「賽後先有名單」洩漏。

本文件只定來源同規則，**唔生成 δ、唔碰凍結預測、唔碰指紋**。

## 各聯賽公布時刻（官方口徑）

| 聯賽 | 官方公布時刻 | 來源性質 | 備註 |
| --- | --- | --- | --- |
| 英超 E0 | 開賽前約 **75 分鐘** | 官方 app／網站同時放全場先發 XI（2024/25 起由 60 分鐘放寬到 75）[1](https://www.premierleague.com/en/news/4081650) [2](https://www.standard.co.uk/sport/football/premier-league-rule-change-early-team-news-b1164931.html) | 早過我哋 60 分鐘鎖定，理論上可用 |
| 德甲 D1 | 一般開賽前 **60–75 分鐘** | DFL／俱樂部官方頻道 | 各場浮動，逐場記實際 ts |
| 西甲 SP1 | 一般開賽前 **60–75 分鐘** | LaLiga／俱樂部官方 | 同上 |
| 意甲 I1 | 一般開賽前 **60–75 分鐘** | Lega Serie A 官方名單表 | 同上 |
| 法甲 F1 | 一般開賽前 **60–90 分鐘** | LFP／俱樂部官方 | 同上 |

> 除英超之外，其餘四個聯賽官方冇公開承諾固定分鐘數，浮動大。
> 因此**唔准用「聯賽慣例分鐘」推算 ts**，只准記錄實際抓到嘅時間戳。

## 聚合來源（先後次序）

1. **聯賽／俱樂部官方頁或官方 app API** — 最權威，但唔一定帶機讀 `published_at`；
   要靠我哋抓取輪次自己記 `observed_at`（見下）。
2. **API-Football（免費層）** — 有 `fixtures/lineups` 端點，但名單通常**開賽前 20–40 分鐘**才上線，
   而且免費層有每日呼叫上限，官方文件冇承諾提前時間 [3](https://www.api-football.com/documentation-v3)。
   即係多數場次會**遲過 60 分鐘鎖定線** → 該場 `eligible=false`、Δλ=0。
3. **TheSportsDB** — 覆蓋唔穩、冇可信公布時間戳 → 只作缺場補洞查證，**唔准做 δ 開關**。

## 時間戳定義（寫死）

- `lineup_published_ts`：上游明確提供嘅公布時間；冇提供就 `null`。
- `lineup_observed_ts`：我哋抓取輪次第一次見到完整名單嘅時間（保守上界）。
- 判定用 `effective_ts = lineup_published_ts ?? lineup_observed_ts`。
- `lead_minutes = (kickoff_utc − effective_ts) / 60`。

## 遲公布 / 缺失標記

| 情況 | status | eligible | 處理 |
| --- | --- | --- | --- |
| `lead_minutes ≥ 60` 且 11 人齊 | `ok` | true | 將來准入 δ_squad（現階段仍 Δλ=0） |
| `0 ≤ lead_minutes < 60` | `late` | false | 遲過鎖定線，Δλ=0，場次標紅燈 |
| `lead_minutes < 0`（開賽後才見） | `post_kickoff` | false | 永不入 δ、永不入戰績 |
| 兩隊有一隊唔齊 11 人 | `incomplete` | false | Δλ=0 |
| 完全冇名單 | `missing` | false | Δλ=0 |
| 隊名對唔上凍結列 | `unmatched` | false | Δλ=0，寫 unmatched 待人手對名 |

**唔准做嘅事**：唔准用平均陣容、上仗 11 人、近況推斷頂替缺失名單（等於偷近況）；
唔准用聯賽慣例分鐘倒推 ts；唔准用完場數據回填賽前名單。

## 一併定義嘅其餘三項（同一套遲到規則）

- **穩定分鐘** `player_minutes`：滾動窗內每人出場分鐘，只用已完場資料，賽前只讀窗口截至上一場。
- **賽前 projected xG** `projected_xg`：只收明確標為賽前、且帶 `source_ts` 早過鎖定線嘅值；
  完場 xG 永不倒算賽前盤。
- **門將撲救** `gk_saves`：滾動窗撲救率／面對射門數，同穩定分鐘同一窗口規則。

四張表任何一張缺 → 對應 δ **唔生成**（Δλ=0），唔用替代值填數。
