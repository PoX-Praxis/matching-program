# PoX 引き継ぎ・設計書 — 自宅Nomic + v4照合 統合（2026-07）

このセッションで「①v4.2 構造化プロンプト → 各自AI生成の必要像 → PoX受け取り検証 → 自宅Nomicで
ベクトル化 → v4照合」の本線を**本番で通した**。その as-built 構成・変更履歴・残タスクを引き継ぐ。

---

## 1. 何を達成したか（サマリ）

- **推論サーバー（Nomic）を Render($85/4GB) から自宅Windows PCへ移設**し、Cloudflare Tunnel 経由で
  Render の pox-web から安全に呼べる状態にした（自宅IP・ポート非公開、ルーター無開放、月額≒電気代）。
- pox-web を実 Nomic バックエンドへ切替、pox-db を **768次元/nomic-emb-v2** 用に再構築。
- 登録ページの構造化プロンプトを **v4.2（必要像生成統合版）** に更新。
- `/v4/seekers` に**アダプタ**を追加し、v4.2の入れ子JSONを直接受理可能に。
- UI登録（/seekers）に **dual-write** を追加：v3保存と同時に裏で v4/Nomic 取り込みを起動。
- トップに **「照合」ボタン**（v4マッチ）を追加。v3「マッチング実行」と並存。
- **実データ（handle `kaoru`）で登録→ベクトル化→`ready` を確認済み**。

---

## 2. As-built アーキテクチャ

```
[利用者ブラウザ]
      │
      ▼
  pox-web  (Render, Web Service, plan=free, gunicorn)
   - Flask 本体（/register /seekers /match /v4/seekers /v4/match ...）
   - POX_EMBED_BACKEND=nomic → 埋め込みは下記へ委譲
      │  HTTPS + X-API-Key
      ▼
  Cloudflare Tunnel   https://nomic.pox-praxis.com/embed
   - ダッシュボード管理トンネル（cloudflared が自宅PCで常駐）
   - Bot Fight Mode / Browser Integrity Check は **OFF**（API用ドメインのため）
      │  localhost:8002
      ▼
  自宅Windows PC   nomic_server（waitress で常駐・NSSMサービス化）
   - モデル: nomic-ai/nomic-embed-text-v2-moe（768次元, Apache-2.0）
   - NOMIC_TORCH_DTYPE=float32（8GB機のため）/ NOMIC_WARMUP=1
   - スリープ無効化・PC起動時に自動起動・落ちても自動再起動

  pox-db  (Render, PostgreSQL, plan=Basic〈有料化済〉, pgvector)
   - profiles_v4 / profile_vectors(768) / derived_necessity(履歴) / ledger_v4
   - + 既存 v3 テーブル（seekers/profiles/... 並存）
```

**重要**: pox-web と pox-db は Render 上で稼働。Nomic **だけ**が自宅PC。自宅PCが停止/ネット断だと
`/v4/seekers`・照合が `error` になる（登録・照合が Nomic 依存）。v3 マッチング・マイページ表示は無関係。

---

## 3. 環境変数（値は各自管理・ここには書かない）

### pox-web（Render → Environment）
| Key | 値 | 意味 |
|---|---|---|
| `DATABASE_URL` | (Render自動) | pox-db 接続 |
| `POX_EMBED_BACKEND` | `nomic` | 実Nomicへ切替済み |
| `POX_NOMIC_ENDPOINT` | `https://nomic.pox-praxis.com/embed` | 自宅Nomicの公開URL |
| `POX_NOMIC_API_KEY` | (秘密・自宅と同値) | `X-API-Key` で送る。**自宅の NOMIC_SERVER_API_KEY と一致必須** |
| `POX_EMBED_MODEL_TAG` | `nomic-emb-v2` | プールの model_tag |
| `POX_EMBED_FULL_DIM` | `768` | 次元 |
| `ANTHROPIC_API_KEY` | **不要**（未設定でよい） | v4.2登録なら②生成にClaude不要。§8参照 |
| ~~`POX_ALLOW_VECTOR_TABLE_REBUILD`~~ | **削除済** | 768再作成は完了。残すと危険 |

### 自宅PC（NSSMサービス `PoXNomic` の環境変数）
| Key | 値 |
|---|---|
| `NOMIC_SERVER_API_KEY` | (秘密・pox-web と同値) |
| `NOMIC_MODEL_TAG` | `nomic-emb-v2` |
| `NOMIC_DIM` | `768` |
| `NOMIC_TORCH_DTYPE` | `float32` |
| `NOMIC_WARMUP` | `1` |
| `HF_HOME` | `C:\pox\hf-cache`（モデル永続キャッシュ） |

---

## 4. 主要エンドポイント

| メソッド/パス | 役割 |
|---|---|
| `GET /register` | 登録ページ（v4.2 構造化プロンプトを内蔵） |
| `POST /seekers` | v3登録の受け口。**v4形JSONなら dual-write で v4/Nomic 取り込みも非同期起動** |
| `POST /v4/seekers` | v4取り込み。**アダプタで v4.2 入れ子JSON も平坦JSONも受理**。必要像同梱=user-supplied / 無=fallback |
| `GET /v4/seekers/<id>/status` | 非同期生成の状態（preparing/ready/error/needs_regeneration） |
| `POST /v4/seekers/<id>/retry` | 失敗した生成の再試行（指数バックオフ） |
| `POST /v4/match` | v4照合。`migrate_pool:true` で既存v3プールを Nomic で一括ベクトル化（バックフィル）してから照合 |
| `POST /match` | v3 マッチング（Claude judge、want トグル、旧方式） |

---

## 5. データフロー（登録→ベクトル化→照合）

1. **登録**: 利用者が自分のAIで v4.2 プロンプトを実行 → 純JSON（seeker/現状/supporting_material/necessity）
   を得る → 登録ページに貼る（`/seekers`）または `/v4/seekers` にPOST。
2. **受付（同期）**: アダプタで平坦化 → `build_user_necessity` で**検証・クランプ**（各自AI出力を無検証で
   信頼しない）→ `compute_gamma` でγ算出（供給gammaは不採用）→ profiles_v4 + derived_necessity 保存
   （status=preparing）。
3. **ベクトル化（非同期）**: 自宅Nomicで4ベクトル（will_symmetric/will_passage/state_passage/
   necessity_query, 各768）生成 → profile_vectors 保存 → status=ready。
4. **照合**: `/v4/match` が保存済みベクトルを**フル次元総当たり**で比較。3チャネル
   （a=共鳴 will↔will / b=補完 necessity↔state / c=意志相補）を necessity の γ,p,α,β で結合 →
   総合スコア＋**律速軸**（最も足を引っぱった経路）。ledger_v4 に監査記録。

---

## 6. このセッションの変更履歴（main へマージ済）

| PR | 内容 |
|---|---|
| #4 | `nomic_server/requirements.txt` に **einops** 追加（Render起動失敗の修正・後に自宅へ移設） |
| #5 | Nomic を **bfloat16** 読み込み（OOM対策の試み） |
| #6 | torch_dtype に **torch.dtype型**を渡す修正（文字列だと TypeError） |
| #7 | `/v4/seekers` に**アダプタ** `_normalize_v4_body`（v4.2入れ子JSON受理） |
| #8 | 登録ページの構造化プロンプトを **v4.2** に更新 |
| #9 | `/seekers` に **dual-write**（v3保存＋裏でv4/Nomic取り込み） |
| #10 | トップに **v4照合ビュー**（「照合」ボタン・migrate_pool バックフィル込み） |
| #11 | v4マッチのボタン表示を「**照合**」に（画面文言のみ） |

関連手順書（`docs/`）: `deploy_local_nomic.md`（自宅Windows・採用構成）、`deploy_hetzner_nomic.md`（VPS案・不採用）、
`render_fullsetup_guide.md`、`deploy_2a_db768_rebuild.md`、`deploy_2a_nomic_service.md`。
束2a+2b の設計は `spec/necessity_gen_prompt_v1.md`（②正本）と各 `src/` 実装。

---

## 7. 現在の状態（動くもの）

- ✅ 自宅Nomic 常駐（再起動・放置で自動復帰）／Cloudflare Tunnel 固定URL
- ✅ pox-web 実Nomic接続・pox-db 768化
- ✅ v4.2プロンプト（/register）
- ✅ 登録→Nomicベクトル化（curl直・UI dual-write の両方で `ready` 確認）
- ✅ 照合（UI・バックフィル込み）
- ✅ 全171テスト PASS（branch `claude/continuation-2k4jfj`、main へ順次マージ）

---

## 8. ②生成に Claude は不要（設計上の重要点）

サーバーが Claude(`ANTHROPIC_API_KEY`)を呼ぶのは **necessity が同梱されない時だけ**:
- `/v4/seekers` フォールバック（必要像なし登録）
- 既存v3ユーザーのバックフィル（migrate_seeker → generate_necessity）
- 旧「マッチング実行」(v3 connection_layer)

→ **全員 v4.2 で登録（必要像は各自AIが生成）＋旧マッチング引退**にすれば、**PoXはClaudeを一切呼ばない**。
`ANTHROPIC_API_KEY` は未設定でよい。唯一の含意：照合のバックフィルはClaude無しだと **demo（非意味）necessity**
になるため、既存ユーザーは **v4.2で登録し直す**のが本筋（Claude不要＋良い必要像）。

---

## 9. マイページ 表示/非表示（別トーク考察用リファレンス）

データ源: `/api/profile/<id>` → `get_profile_view()`（公開ビュー。seeker原文・necessity は出さない設計）。

### A. 表示中（v4）
`headline` / `pursuing`(意志) / `state_have` / `state_can_type` / `state_bound` / `seeking`(求めている) /
`keys` / `trajectory`。

### B. profile_view に有るが未描画（出すのは容易）
`state_unsorted`（現状「未分類」）。

### C. v4 DBに有るが profile_view に出していない
- **②所有の必要像・数値**（`derived_necessity`）: `necessity_text` / `gate_s` / `gate_u` / `gamma` /
  `p_sharpness` / `alpha` / `beta` / `evidence_span` / `generator_name`。
- **エンジン状態・ベクトル**: `generation_status`(照合準備状態) / `generation_error` /
  4本の768次元ベクトル / `migrated_from`。
- **supporting_material 生データ**: 生テキスト / 要約文 / 意志要求の素材 / 連言選言の素材 /
  チャネル重み素材 / `supporting_redacted`。

### 非表示の理由（3種）と論点
1. 意図的に隠す（privacy/設計・CLAUDE.md制約）: necessity・seeker原文・素材の生データ。
2. エンジン内部で見せる意味が薄い: ベクトル・gamma・p/α/β。
3. 未実装だが出せば有用: **`generation_status`（低リスク・有用）** / `state_unsorted`。

**検討の中心**: 「本人にだけ見せてよいもの（generation_status・自分のnecessity）」と「誰にも公開しないもの
（ベクトル・他人の素材）」の線引き。特に necessity_text は「本人に見せる価値 vs 簡易ログインゆえ
他人にも漏れる懸念」のトレードオフ。

---

## 10. 残タスク（優先度順）

- 🔴 **完了**: pox-db 有料化（7/20削除回避）。
- 🟡 **運用前提**: 自宅PC＋トンネル常時稼働（止まると登録・照合が error）。有線推奨。
- 🟢 **品質（実ユーザー前）**:
  - 既存ユーザーを **v4.2 で登録し直し**（バックフィルの demo necessity より高品質・Claude不要）。
  - テストデータ掃除（`smoke_test` / `kaoru`）※psql必要・エラーではなく照合候補の汚れ。
- ⚪ **判断（急がない）**:
  - **マイページ v4対応**: 照合準備状態(generation_status)表示＋「v4.2で作り直す」導線。必要像を本人に
    見せるかは要判断（§9）。
  - 旧「マッチング実行」(v3) を撤去して照合一本化。
  - 照合バックフィルの非同期化（登録者増で初回照合が重くなったら）。
  - PII/raw ポリシー確定（redaction 方針）。

---

## 11. セキュリティ制約（維持）

- `ANTHROPIC_API_KEY` / `GITHUB_TOKEN` / `DATABASE_URL` / 各APIキーはコード・リポジトリに書かない。
- `seeker` 原文・`necessity` は viewer/public API に出さない。`GET /profile` は `get_profile_view()` のみ。
- 各自AI出力（必要像）は**無検証で信頼しない**：`_validate` で範囲クランプ、γは `compute_gamma` 算出。
- 本番DBの破壊的再作成は 0件確認 + `POX_ALLOW_VECTOR_TABLE_REBUILD=1` のときのみ（現在フラグ削除済）。
- Cloudflare の Bot Fight Mode / Browser Integrity Check は **OFF のまま**（戻すと Render→Nomic が403）。
- 自宅Nomic / pox-web の APIキーは**同値**を維持（不一致で401）。

---

## 12. 再開の起点（別トークへ）

- ブランチ: `claude/continuation-2k4jfj`（main へ随時マージ）。
- 次に着手しやすい塊: **マイページ v4対応**（照合状態＋re-register導線）か、**旧マッチング引退**、
  または **テストデータ掃除**。§10 参照。
- 通し確認の型: 自分のAIで v4.2 プロンプト → 純JSON → `/v4/seekers`（または登録UI）→
  `/v4/seekers/<id>/status` が `ready` → トップで「照合」。
