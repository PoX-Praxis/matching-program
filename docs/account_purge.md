# アカウント整理（指示書55-3 §2・一回限り）

> **HTTP のルートは閉じた（指示書58 §2-4）。** 以下の手順 2・3 は、ルートが開いていた時点の記録。
> 同じ処理は `scripts/purge_accounts.py`（CLI。既定 dry-run・`--apply` で実行）で行える。

対象は **`kaoru`・`smoke_test`・`u_5672a380`・`u_9fa00efa` の 4 件に固定**（`scripts/purge_accounts.py` に
ハードコード）。いずれも 2026-09-30 の inventory で「本人と紐づいていない・台帳参照 0」を確認済み。

## 手順（PowerShell）

1. **ダンプを取る**（必須）。Render の Postgres の External Database URL を使う。
   ```powershell
   pg_dump "<External Database URL>" -Fc -f pox_before_purge.dump
   ```
2. **確認だけ（dry-run）**。消える行数と、消せない理由（`blocked`）を見る。
   ```powershell
   $t = Read-Host
   Invoke-RestMethod -Method Post -Uri "https://pox-web.onrender.com/ledger/admin/purge-accounts" -Headers @{ "X-Anchor-Token" = $t } -ContentType "application/json" -Body '{}' | ConvertTo-Json -Depth 6
   ```
3. **実行**（`blocked` が無いことを確かめてから）。
   ```powershell
   Invoke-RestMethod -Method Post -Uri "https://pox-web.onrender.com/ledger/admin/purge-accounts" -Headers @{ "X-Anchor-Token" = $t } -ContentType "application/json" -Body '{"apply": true}' | ConvertTo-Json -Depth 6
   ```
4. `deleted_ids` と `profiles_v4_remaining`（2 になる見込み）を報告する。
5. **ルートを閉じる PR を出す**（inventory・`/ledger/audit/match` と一緒に）。→ 閉じた（指示書58）。

## 規則

- 実行時に対象ごとに台帳参照を再確認し、0 でなければ消さない（非表示の扱いに回す）。本人と紐づいた id・
  コミュニティのメンバー記録がある id も消さない。
- 削除は 1 トランザクション。台帳（ledger_events）には触らない。
- 併せて消すもの: v4（`profiles_v4`・`profile_vectors`・`derived_necessity`）、`ledger_v4` の照合記録、
  旧 v3（`seekers`・`profiles`）、スナップショット・表示名・ハンドル・下書き・申し出・DM・必要像。
