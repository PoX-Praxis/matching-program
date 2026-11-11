# 報告 repo-operations — リポジトリベースの運用の土台づくり

- 指示書: `docs/instructions/repo-operations.md`
- PR: #164（未マージ。指示書のとおりマージしない）
- 日付: 2026-11-08
- 書いた人: Claude Code

---

## やったこと

ブランチ `docs/repo-operations` で、項目ごとにコミットを分けた。

| 項目 | コミット | 中身 |
|---|---|---|
| 土台 | `8700ab8` | `docs/status-report.md`（2026-11-08 の棚卸し） |
| 指示書 | `9aafa70` | `docs/instructions/repo-operations.md`（受け取った本文のまま） |
| 1 | `30302f3` | `docs/OPERATIONS.md` |
| 2 | `6149ca2` | `docs/instructions/_template.md`・`docs/reports/_template.md`・`docs/decisions/_template.md` |
| 3 | `9691906` | `docs/status.md`（41 行） |
| 4 | `3dd0966` | `docs/decisions/001〜016`（すべて「下書き（要確認）」）と一覧 `docs/decisions/README.md` |
| 5 | `552e292` | `README.md` の書き換え・`docs/history/README_2026-06.md`（旧い README の本文をそのまま移した）・`CLAUDE.md` の書き換え |
| 6 | `c47ecd8` | 時点の記録 7 文書の冒頭に 1 行（本文は変えない） |
| 7 | `b66321e` | `docs/instructions/README.md`・`docs/reports/README.md` |
| 報告 | この PR の最後のコミット | この報告と、`docs/status.md`・2 つの README の一覧の更新 |

**指示書に無いことで、足したもの**
- **指示書の本文を最初のコミットで入れた。** この PR で決める「PR の最初のコミットで指示書を入れる」に合わせた
- **`docs/decisions/README.md`（判断の一覧）を足した。** 状態をまとめて見られるようにするため
- **PR の土台:** `docs/status-report.md` は main に入っていなかった。そのため、それを含む `claude/continuation-2k4jfj` から枝を切った。PR には棚卸しのコミットも入っている
- **作業の途中で、作業の環境が入れ替わった。** push していなかった 2 コミット（指示書の本文・OPERATIONS）が消えたので、同じ中身で作り直した。以後はコミットのたびに push した

**項目 5（CLAUDE.md）で引き継いだもの・足したもの・消したもの**
- **引き継いだ（旧い CLAUDE.md から）:**
  - 鍵を書かない
  - `.env` などをコミットしない
  - `*.db` は無視済み
  - `seeker` の列を出さない
  - `GET /profile/<id>` は `get_profile_view()` だけ
- **足した（これまでの指示書の決まりから）:**
  - PII をマスクしてから送る
  - 数値を画面と公開 API に出さない
  - トークン・メールアドレス・生テキスト・根拠の本文・`evidence_span` を報告やログに出さない
  - 個人の文は `local/`
  - 本人以外の数値を書かない
  - 本番の DB は読み取りだけ
  - プロンプトは `docs/prompts/` が唯一の正
  - テンプレートで `prompt(`・`confirm(` を使わない（テスト 63）
- **消した:**
  - Qwen3 の「継続中タスク」
  - 「完了済みタスク」
  - 古い「主要ファイル構成」
  - v4／v3 の profile_view のフィールドの一覧

**項目 6 で「いまの正」として指した先**

| 文書 | 時点 | いまの正 |
|---|---|---|
| `docs/HANDOVER_2026-07_nomic_v4.md` | 2026-07 | `docs/status.md`・`docs/decisions/`・`docs/matching.md` |
| `docs/production_design_notes.md` | 2026-07 | `docs/status.md`・`docs/matching.md`・`src/embedding_config.py` |
| `docs/spec_v0.8_patch.md` | 2026-07 | `docs/status.md`・`docs/trajectory.md`・`docs/decisions/` |
| `spec/structuring_prompt_v4_interview.md` | 2026-06 | `docs/prompts/` の 2 ファイル |
| `docs/approval_flow_todo.md` | 2026-09 | `docs/status.md` |
| `docs/deploy_2a_nomic_service.md`・`docs/deploy_hetzner_nomic.md` | 2026-07 | `docs/deploy_local_nomic.md`（`render.yaml` の注記にもとづく） |

時点の年月は、各文書の最後の変更のコミットの月とした。

## 結果

- 項目 1〜7 は、それぞれ別のコミットになっている（上の表）
- テスト: `python -m pytest -q` で **905 passed, 2 skipped**（手元。文書だけの変更）。PR の CI の結果は PR #164 で確かめる
- 変えていないもの:
  - `src/`・`app.py`・`templates/`・`scripts/`・`tests/`・`render.yaml`・設定ファイル
  - 削除したファイルは無い
  - 旧い README は、本文を `docs/history/` に写してから `README.md` を書き換えた
- 書いていないもの: `local/` の中身・個人の文・生テキスト・根拠の本文・計測の個別の数値
- **まだやっていないもの:**
  - 過去の指示書（06〜66）と報告の取り込み（項目 7 のとおり後から）
  - 計測（65-A・66）の集計を `docs/measure/` に置くこと

## 判断が必要な点

### decisions/ の下書きのうち、確認が要るもの

すべて「下書き（要確認）」。特に次のものは、中身に確かめていない部分がある。

| 番号 | 確認してほしいこと |
|---|---|
| **001** | 理由の【推測】（「サーバーに LLM の費用と責任を持たせないため」）が合っているか。例外（v3.1 → v4 の遅延移行で、サーバー側で必要像を作る経路が残っている）を残すか、塞ぐか |
| **008** | 理由の【推測】（「もっともらしい作り話を見せないため」）が合っているか |
| **010** | 捨てた選択肢が【推測】（「数値を見ながら作動点を選ぶ進め方」）のまま |
| **013** | メールの暗号化保存（`email_enc`）をやめた理由の記録が見つからない |
| **016** | 捨てた選択肢が【推測】。古い経路を閉じたまま残すか、消すか（`docs/status-report.md` §8） |
| **002・003・015** | 捨てた選択肢が「記録なし（要確認）」 |
| **006・011** | 判断と実際の設定が食い違っている。直すなら、別の指示書でコードと設定を変える<br>・006: `src/embedding_config.py` の既定値が Qwen3 のまま<br>・011: `render.yaml` に使わない cron が残っている |
| **009** | 上層の連言を「必須ごとに別の文」にする案の採否が、未定のまま書いてある |
| **全部** | 「決めた人」を「未確認」にしてある。KAORU が決めたもの、Sakana や Claude（チャット）の提案を KAORU が確かめたものを、書き分けるか |

### OPERATIONS.md で Code が決めたこと

指示書に無い細部を決めた。合っているか確かめてほしい。

- 追補のファイル名を `<番号>-addendum<n>.md` にした。番号の無い指示書は、英小文字の識別子にした
- 判断のファイル名を `<3 桁>-<英小文字>.md` にした。「確定」は判断する人（KAORU）が確かめたもの、とした
- 1 つの指示書で報告を何回か出すときは、枝番を付ける（例: `66-PR-66B.md`）
- **月 1 回の棚卸し:** `docs/status-report.md` を書き直し、冒頭に前回との差分を書く。前回の本文は git の履歴で見る
  - 別案: 月ごとに別ファイル（例 `docs/inventory/2026-11.md`）にすると、履歴を見ずに比べられる
- Notion は、本文を写さずにページ名で参照する

### CLAUDE.md

- 足したセキュリティの制約（上の「やったこと」）が合っているか
- 消した v4／v3 の profile_view のフィールドの一覧を、どこかに残すか（例: スキーマの文書）

### 次の作業

- **過去の指示書と報告の取り込み:** 本文はチャット・アップロード・作業したセッションの作業領域にある
  - 個人の文（抜き取りの票・上層を通った人の文の一覧など）を含むものがあるので、伏せる作業が要る
  - 誰が本文を渡すか、どの範囲から入れるかを決める
- **計測の集計を `docs/measure/` に置くか:** 65-A・66B・66C の判定・曲線・材料の識別子・再現方法
- **`docs/status-report.md` §8 の整理:** 古い経路・未使用のコード・設定の食い違い
  - コードと設定を変えるので、別の指示書が要る
- **ブランチの後始末:** この PR がマージされたら、`claude/continuation-2k4jfj` を main にそろえる

## status.md への反映

- 「進行中」の運用の土台を「PR #164・マージ待ち」にした
- 「判断待ち」は変えていない（decisions/ の下書き 001〜016 の確認・過去の指示書と報告の取り込みは、すでに載っている）
