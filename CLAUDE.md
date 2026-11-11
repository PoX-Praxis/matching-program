# PoX matching-program — AI が作業するときの決まり

この文書は、決まり事と道案内だけを書く。現状と中身は、ほかの文書にある。

## 作業の前に読む

1. `docs/status.md`: いまの現状（1 ページ）
2. `docs/OPERATIONS.md`: 運用の決まり（指示書・報告・判断・計測の置き方、入れてはいけないもの）
3. 作業に関係する `docs/decisions/`（一覧は `docs/decisions/README.md`）
4. 作業の指示書 `docs/instructions/<番号>.md` と、関係する前の報告 `docs/reports/`

詳しい棚卸しは `docs/status-report.md`、本体の説明は `README.md`。

## リポジトリ

- GitHub: `pox-praxis/matching-program`。正のブランチは `main`。作業はブランチを切って PR にする（`docs/OPERATIONS.md` §2）
- 本番: Render の `pox-web`（`render.yaml` のサービス名）と Postgres

## セキュリティの制約（絶対厳守）

**秘密**
- `ANTHROPIC_API_KEY`・`GITHUB_TOKEN`・`DATABASE_URL` などの鍵と値は、コードにもリポジトリにも書かない
- `.env`・`*.key`・`secrets.json` はコミットしない。`pox.db`・`*.db` は `.gitignore` 済み

**本人の層と公開の層**（`docs/decisions/003`）
- `seeker` の列は、viewer の API と公開 API のレスポンスに絶対に出さない
- `GET /profile/<id>` は、必ず `get_profile_view()` だけを呼ぶ（`get_seeker()` は呼ばない）

**PII**（`docs/decisions/004`）
- 埋め込み・LLM に送る文は、`pii_redaction` でマスクしてから送る。生の文を送らない

**数値を出さない**（`docs/decisions/007`）
- 照合の数値（g・スコア・gate・閾値）は、画面と公開 API に出さない。監査ルートだけ

**報告・ログ・リポジトリに入れないもの**（`docs/OPERATIONS.md` §5）
- トークン・メールアドレス・生テキスト・根拠の本文・`evidence_span`
- 個人の文（KAORU・PA など、実在する人の文）。手元の `local/`（`.gitignore` 済み）に置き、コミットしない
- 本人以外の数値（PA の gate など）。計測の個別の数値

**本番**
- 本番の DB は読み取りだけ。`DATABASE_URL` をセッションに渡さない
- 指示書が求めない限り、本番（Render・DB・台帳・トークン）に触れない

## 実装の決まり

- 構造化のプロンプトは `docs/prompts/` が唯一の正。画面はそこから読み込む（画面に直書きしない）
- 画面のテンプレートでは `prompt(`・`confirm(` を使わない（テスト 63）
- 古い経路は、指示書が求めない限り、消さずに閉じる（`docs/decisions/016`）
- 計測の規則は、計測の前にコミットする。数値を見た後に変えるときは、規則の変更として記録し、確認を取る（`docs/decisions/010`）

## 作業の終わり

- 報告を `docs/reports/<番号>.md` に書き、`docs/status.md` を更新する（PR の最後のコミット）
- 判断をしたら `docs/decisions/` に 1 ファイル足す
