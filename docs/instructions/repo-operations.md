# 指示書 repo-operations — リポジトリベースの運用の土台づくり

- 受け取った日: 2026-11-08
- 番号: なし（識別子 `repo-operations`）
- 報告: `docs/reports/repo-operations.md`
- 本文は受け取ったまま（除外に当たる部分は無い）

---

# 指示書：リポジトリベースの運用の土台づくり

## 目的
引き継ぎ無しで、どの AI もこのリポジトリから現状と経緯を読み取れるようにする。
土台は docs/status-report.md（2026-11-08 の棚卸し）。

## 禁止事項
- src/・app.py・templates/・scripts/・tests/・render.yaml・設定ファイルは変更しない
- ファイルの削除はしない
- local/ の中身、個人の文（KAORU・PA の文など）、生テキスト、根拠の本文、
  計測の個別の数値は、どのファイルにも書かない
- 本番（Render・DB）には触れない

## 進め方
- 新しいブランチ docs/repo-operations で作業し、下の項目ごとにコミットを分ける
- 最後に PR を作る（マージはしない）

## 作業

### 1. 運用ルール docs/OPERATIONS.md
次の決まりを書く。
- 1 指示書 ≒ 1〜数 PR
- PR の最初のコミットで指示書を docs/instructions/<番号>.md に入れる
- PR の最後のコミットで報告を docs/reports/<番号>.md に入れ、docs/status.md を更新する
- 判断をしたら docs/decisions/ に 1 ファイル追加する
- 個人の文はリポジトリに入れない。計測は集計・判定・使った材料の識別子だけを
  docs/measure/ に置き、材料の置き場所と再現方法を書く
- 情報の持ち主：Notion＝思想と背景、リポジトリ＝実装・判断・現状
- 月 1 回、棚卸しを行い、前回の報告との差分を見る

### 2. テンプレート
- docs/instructions/_template.md（目的・前提・作業・禁止事項・完了条件）
- docs/reports/_template.md（やったこと・結果・判断が必要な点・status.md への反映）
- docs/decisions/_template.md（決めたこと・理由・捨てた選択肢と理由・影響範囲・状態）
  状態は「確定／下書き（要確認）／置き換え済み」のどれか

### 3. docs/status.md
status-report.md をもとに、1 ページ以内で書く。
- いまの本体の構成（v5 の流れ：構造化 → nomic の非対称検索 → 上層の判定 → 引用の根拠）
- 動いているもの／閉じているもの／止まっているもの
- 進行中の作業（66D・65-C rev5・PR #138）
- 判断待ちの点
チャットに貼って現状を伝えるために使うので、短さを優先する。

### 4. 判断の初期セット docs/decisions/
status-report.md の §7 の各行を 1 ファイルずつ起こす（001〜）。
- 状態はすべて「下書き（要確認）」にする
- 理由のうち【推測】の部分は、推測と明記したまま残す
- 根拠となる指示書番号・PR 番号があれば書く

### 5. 入口の文書を作り直す
- README.md：いまの本体（Flask・v5・nomic・台帳・Render）の説明に書き換える。
  旧い内容は docs/history/README_2026-06.md に移す
- CLAUDE.md：決まり事と道案内だけにする。中身は次のとおり
  - 作業の前に docs/status.md と docs/OPERATIONS.md を読む
  - 関係する docs/decisions/ を確認する
  - セキュリティの制約（本人の層と公開の層・PII・数値を出さない、など）は
    いまの CLAUDE.md から正しいものを引き継ぐ
  - 古い記述（Qwen3 が本番、など）は消す

### 6. 時点の記録に印を付ける
次の文書の冒頭に「時点の記録（YYYY-MM）。いまの正ではない。いまの正は○○」と書き足す。
本文は変えない。
- docs/HANDOVER_2026-07_nomic_v4.md・production_design_notes.md・spec_v0.8_patch.md
- spec/structuring_prompt_v4_interview.md（v5 のほうには既にある）
- docs/approval_flow_todo.md
- docs/deploy_2a_nomic_service.md・deploy_hetzner_nomic.md（未使用の別案）

### 7. 指示書と報告の置き場所
docs/instructions/ と docs/reports/ に README.md を置き、
過去の指示書（06〜66）と報告は後から入れること、入れるときの除外の決まりを書く。
本文はリポジトリの外にあるので、今回は入れない。

## 完了条件
- 上の 1〜7 がそれぞれ別のコミットになっている
- テストが通る（文書だけの変更なので変わらないはず）
- 報告を docs/reports/repo-operations.md に書き、
  判断が必要な点（特に decisions/ の下書きのうち確認が要るもの）を列挙する
