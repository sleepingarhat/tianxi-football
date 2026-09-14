# 名稱對照

`team_names.csv` / `player_names.csv`：`canonical_id, en, zh_hant_hkjc, zh_hans, source, verified_at`。
馬會官方繁中譯名為唯一權威，簡體欄只用於對齊內地資料源回傳名。未核對者標 `verified_at` 為空並進待審告警。

`clubelo_names.csv`：`fd_div, fd_name, clubelo_name, country, level`。
football-data.co.uk 隊名 ↔ ClubElo 拼法對照，只用於對帳。對唔上就入 `unmapped`／`unmatched`，
由 `scripts/reconcile_elo.py` 出覆蓋率告警，永不 silently 亂配、永不填 0 或用均值頂。
