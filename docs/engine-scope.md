# 訓練與回測層（原 tianxi-football-engine）

> 2026-10-06 起併入統一倉 `tianxi-football`；原倉已封存。

## 職責
- 特徵工程（`docs/football-feature-dict.md` v0 → v2），全部 as-of kickoff，滾動 `shift(1)`，逐季分開
- walk-forward 回測（嚴禁隨機 K-fold），分片執行避免超時
- 閘門表：技術（RPS／Brier／log-loss／逐類別 ECE）＋ 商業（盈利門檻 `1/precision < 命中場均賠率`、
  Profit Balance、yield%、ROI、最大回撤、平均賠率、盈虧比）
- 每加一組特徵要交凍結點快照證明；三項任一未升即不上線

## 基準（S1，238,854 場 / 2000–2026 / 38 聯賽）
| 基準 | RPS | Brier | log-loss | ECE |
|---|---|---|---|---|
| uniform | 0.2247 | 0.3333 | 1.0986 | 7.49% |
| prior_asof | 0.2261 | 0.3234 | 1.0699 | 0.87% |
| market_devig（僅對照） | 0.2047 | 0.3009 | 1.0056 | 0.87% |

## 狀態
S0 骨架，下一步 S2 天喜足球ELO。
