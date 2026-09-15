# 場次條件層 Δλ 表結構（S27）

三張只附加（append-only）表，落 `data/delta/` 為 JSONL。
每行一條紀錄，**永不 UPDATE、永不刪行**；改正靠寫新一行（`supersedes` 指向舊 `record_id`）。

## 硬規則

1. Δλ 表**唔可以**寫入 `data/predictions/`、`models/`、`snapshots/` 或任何指紋欄。
2. 凍結預測列（`p`、`lambda`、`cs`、`fingerprint`、`locked_at`）唔准俾 Δλ 改動。
3. 缺資料 → Δλ = 0，`status = "missing"`，退回基準 λ，場次標紅燈。
4. 紅燈場可查、可睇，唔入戰績、唔入對帳分母。
5. 現階段只落結構，**唔估 λ**：所有 `delta_h` / `delta_a` 一律寫 0，
   `applied = false`，直到閘門通過為止。

## 共用欄位

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `record_id` | string | UUIDv4，唯一 |
| `match_key` | string | 對應凍結帳 match_key |
| `div` | string | E0/D1/SP1/I1/F1 |
| `kickoff_utc` | string | ISO8601 |
| `written_at` | string | ISO8601，寫入時間（唔等於資料時間） |
| `source` | string | 資料來源標識（授權來源名） |
| `source_ts` | string\|null | 上游資料時間戳；缺 = null |
| `status` | string | `ok` / `missing` / `stale` / `unmatched` |
| `delta_h` | number | 主隊 λ 調整量；現階段固定 0 |
| `delta_a` | number | 客隊 λ 調整量；現階段固定 0 |
| `applied` | bool | 是否入生成；現階段固定 false |
| `supersedes` | string\|null | 修正時指向舊 record_id |
| `notes` | string\|null | 自由備註 |

## delta_squad — 名單／陣容

准用前置：**公布名單時間戳**必須早於鎖定時刻（開賽前 60 分鐘）。缺就 `status=missing`、Δλ=0。

額外欄位：`lineup_published_ts`、`missing_starters_h`、`missing_starters_a`、
`minutes_weight_h`、`minutes_weight_a`（穩定分鐘覆蓋率 0–1）。

## delta_density — 賽程密度

額外欄位：`rest_days_h`、`rest_days_a`、`matches_14d_h`、`matches_14d_a`、
`travel_km_h`、`travel_km_a`（缺 = null）。

## delta_market — 市場殘差（診斷用）

**只作診斷，永不回餵模型。** 賠率權重永遠 0。

額外欄位：`novig_p`（[H,D,A] 去水機率）、`model_p`（同批已鎖預測機率）、
`resid`（novig_p − model_p 三項）、`book_count`、`captured_before_lock`（bool）。
`captured_before_lock = false` 嘅紀錄唔准入任何對照表。

## 檔案

- `data/delta/delta_squad.jsonl`
- `data/delta/delta_density.jsonl`
- `data/delta/delta_market.jsonl`
- `data/delta/_schema_version` — 目前 `1`（結構版本，唔係模型指紋）
