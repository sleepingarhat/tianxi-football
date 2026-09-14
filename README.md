# tianxi-football-database

天喜足球引擎 · 資料層（對應賽馬 `tianxi-database`）

## 目錄

```
data/            bronze（源檔快照）／silver（清洗對齊）／gold（特徵表）
scripts/         採集器與驗收／基準腳本
snapshots/       凍結快照（基準線、驗收報告），只增不改
mapping/         team_names / player_names 繁中（馬會官方譯名優先）對照表
.github/workflows/  排程 Actions
```

## 鐵律

1. **賠率零排名權重**：`market_beta = 0`。賠率只入離線對照軌、gate 基準與殘差診斷。
2. **特徵必須賽前可計**（as-of kickoff），滾動一律 `shift(1)`，逐季分開。
3. **嚴禁隨機 K-fold**，一律時序前推（walk-forward）。
4. **禁用欄位**：`ExpectedGoals`（上游用賠率＋賽中統計衍生，非真 xG，賽前不可得）、
   本場完場射門／控球／評分／HT 比分作特徵、賽後版傷停與陣容。
5. 第三方鏡射倉只作**啟動基線＋交叉核對**，長期由官方源自行排程落地。

## S1 基準線快照（2026-09-13，全量 238,854 場 / 2000-07-28 → 2026-09-06 / 38 個聯賽）

| 基準 | n | RPS ↓ | Brier ↓ | log-loss ↓ | Accuracy | ECE(平均) |
|---|---|---|---|---|---|---|
| 均勻猜 uniform | 238,854 | 0.2247 | 0.3333 | 1.0986 | 44.57% | 7.49% |
| 歷史先驗 prior_asof（as-of 累積頻率） | 238,854 | 0.2261 | 0.3234 | 1.0699 | 44.52% | 0.87% |
| 去水賠率 market_devig（**僅對照**） | 235,806 | 0.2047 | 0.3009 | 1.0056 | 50.10% | 0.87% |

讀法：模型必須先打贏 `prior_asof`（S2 gate），最終目標係逼近 `market_devig` 嘅 RPS 0.2047。
`uniform` 嘅 RPS 略優於 `prior_asof` 係有序 RPS 對「和局過度自信」嘅懲罰差異，非錯誤——
所以 S2 起主 gate 用 RPS＋log-loss 雙看，唔單看一項。

逐聯賽／逐賽季明細：`snapshots/baseline_snapshot.csv`；來源與範圍：`snapshots/baseline_snapshot.json`。

## 腳本

| 腳本 | 用途 |
|---|---|
| `scripts/baseline_snapshot.py` | 由賽果＋賠率檔算三條基準（uniform／prior_asof／market_devig）並凍結快照 |
| `scripts/footystats_acceptance.py` | FootyStats API 採購前驗收：聯賽歷史深度、賽前欄位、xG 十項規格、速率上限 |

```bash
python3 scripts/baseline_snapshot.py --matches data/Matches.csv --outdir snapshots
FOOTYSTATS_KEY=xxx python3 scripts/footystats_acceptance.py --league-id 12325 --outdir reports
```

## 資料源次序

1. 免費組合：football-data.co.uk（賽果＋開盤／收盤賠率骨幹）、openfootball、Transfermarkt 開放集、Open-Meteo 天氣、StatsBomb open data（xG）
2. FootyStats API（補近 5／6／10 滾動統計、H2H、盤口統計）— 驗收中
3. TheSports（要中文譯名＋賽前官方陣容＋角球即場才升級）

授權提醒：上游 football-data.co.uk 免費條款屬非商業，收費前需書面確認。
Understat 明文禁止程式存取，未獲書面授權前零排程抓取。


## 凍結帳（prediction_log，S13）

- `data/predictions/upcoming.json`：**只係未開窗口**，每日覆寫，唔係歷史 SSOT。
- `data/predictions/log/YYYY-MM.json`：逐場凍結帳，一場一行（key = `div|DD/MM/YYYY|home|away`）。
  一經鎖定（`locked_at` 已寫）預測欄永不覆寫；上游有新數只會記入 `log/audit.jsonl` 並拒絕改帳。
- 逐場鎖定＝**開賽前 60 分鐘**（`predict_fixtures.py` LOCK_MINUTES = 60）。黃燈可刷新、綠燈已鎖。
- `scripts/settle_predictions.py`：賽果 CSV 入庫後按同一 key join，只寫 `result`
  （ft_h／ft_a／ftr／rps／argmax_hit／p_actual／波膽格排名），預測欄一分不改；對唔上名入 unmatched。
- `data/predictions/hit_rate.json`：公開讀口。頁頂戰績只收五大聯賽、綠燈場；紅燈退回基準軌只作診斷。
- 完場一律讀本帳，禁止用最新模型重打已完場。

### 鎖定政策（唯一口徑）

- 逐場鎖定＝**開賽前 60 分鐘**：之前係黃燈（可刷新），之後係綠燈（已鎖）。
- **已鎖場次永遠跟當時指紋**：重訓／升版只影響之後未鎖嘅場次。
- 已鎖場次遇上新模型：`log_predictions.py` 拒絕改帳，寫 `log/audit.jsonl`
  （`refuse_locked` ＋ `fingerprint_drift`），並出 `snapshots/lock_YYYY-MM-DD.json`。
- `selfcheck.py` 核對 `predict_fixtures` 同凍結帳鎖定分鐘一致，唔一致即列為問題（看門狗開 issue）。

### 對帳指標定義（開帳前寫死，唔事後改）

| 層級 | 指標 |
| --- | --- |
| 主數字 | 平均 RPS（↓）、1X2 校準誤差 ECE、樣本數＋指紋 |
| 1X2 次指標 | argmax 首選命中率 |
| 波膽 | 實際比分落頭八格比例 `cs_top8`、實際格 log-loss `cs_logloss`（↓） |
| 不採用 | 波膽命中率做主數字、紅燈場入樣本、價值注 yield 上第一屏、回測數字填實戰三格 |

`cs_logloss` 罰分底：實際比分跌出頭八格時，用頭八格最細機率的一半（下限 1e-4）。
定義同時寫入 `hit_rate.json.metrics`，公開頁只讀呢份定義。
