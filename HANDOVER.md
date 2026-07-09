# PoX マッチングエンジン 実験フェーズ 引き継ぎ書

**作成日:** 2026-06-26  
**ブランチ:** `exp/embedding-model-eval`  
**状態:** 実験進行中（採用モデル未確定）

---

## 1. 現在地：何をやってどこまで来たか

### やったこと（完了済み）

| 日付 | 内容 | コミット |
|---|---|---|
| 06-25 | pool_eval.py：候補ベクトルをキャッシュ化（540→90回） | `8ce224c` |
| 06-25 | solo_eval.py：seeker 固定マッチングスクリプト作成 | `8e3984c` |
| 06-25 | solo_eval：v4形式 my_profile.json + derived_necessity 対応 | `d188b92` / `c5e70f2` |
| 06-25 | eval/snapshots/ + log.md：実験結果の永続記録基盤 | `8aeed31` / `43386e9` |
| 06-25 | KH × Qwen3 solo eval 第1回実行・スナップショット保存 | `15776bf` |
| 06-26 | EmbGemma / Nomic の prefix 書式を公式準拠で確定 | `f5b5b83` |
| 06-26 | KH × EmbGemma / Nomic solo eval 実行・3モデル比較分析 | `648b588` / `5d995b6` |
| 06-26 | fetch スクリプト改良（帯域分割・過剰収集・スキップ強化） | `3529841` |
| 06-26 | 海外クエリ集合追加（GITHUB_QUERY_SET=global） | `66137b4` |
| 06-26 | プール 90 → 1,090 件へ拡張（日本500 + 海外500） | `c6347e6` |

### 現在の状態

- **プール（候補）:** 1,090 件（`eval/github_profiles.jsonl`・gitignore・PCローカル）
- **実験結果:** `eval/snapshots/` に3件（3モデル×KH・90件プール分）
- **採用モデル:** 未決定（3モデル比較中）
- **次の実験:** 1,090件プールで3モデル再実行（未実施）

---

## 2. システム構成

### マッチングの流れ（3層）

```
① プロフィール登録（意志 + 現状4スロット）
    ↓
② 必要像生成（Claude Opus: generate_necessity）
   → necessity_text / gamma / p_sharpness / alpha / beta を返す
    ↓
③ ベクトル化 × スコアリング（embedding_service + matcher_v4）
   → 4チャネル: will_symmetric / will_passage / state_passage / necessity_query
   → score = f(a_sim, complement, gamma, p, alpha, beta)
```

### ファイル構成（実験フェーズ関係分）

```
src/
  embedding_config.py     # モデル・次元・prefix の単一ソース（★ここを触る）
  embedding_service.py    # build_vectors()：4チャネルベクトル生成
  matcher_v4.py           # rank_candidates()：★絶対に触らない
  match_config.py         # ゲート・パラメータのデフォルト値
  necessity_gen.py        # generate_necessity()：②生成層（Claude Opus）

eval/
  scripts/
    fetch_github_profiles.py  # GitHub プロフィール収集・構造化（Claude Haiku）
    solo_eval.py              # seeker固定マッチング実験
    pool_eval.py              # eval_pairs 全seekerマッチング実験
  github_profiles.jsonl   # プール本体（gitignore・PCローカルのみ）
  my_profile.json         # KHのプロフィール（gitignore・PCローカルのみ）
  snapshots/              # 実験結果の永続記録（git追跡）
  log.md                  # 実験ログ（git追跡）
  eval_pairs_v1.jsonl     # 評価ケース集（6seeker）
```

### 対象3モデル

| MODEL_TAG | FULL_DIM | BACKEND | PORT |
|---|---|---|---|
| `qwen3-embedding-0.6b-d1024` | 1024 | qwen3 | 8000 |
| `embgemma-300m` | 768 | embgemma | 8001 |
| `nomic-emb-v2` | 768 | nomic | 8002 |

※ 各モデルはローカルサーバー（.bat）として起動。同時起動はメモリ不足になるため1つずつ。

---

## 3. 実験結果サマリー（90件プール・第1回）

### 3モデル上位10件

| Rank | Qwen3 | EmbGemma | Nomic |
|---|---|---|---|
| 1 | hoochanlon(0.746) | SakanaAI(0.775) | hoochanlon(0.792) |
| 2 | SakanaAI(0.739) | hoochanlon(0.772) | yuk7(0.782) |
| 3 | yusukebe(0.735) | Jannchie(0.770) | privatenumber(0.781) |
| 4 | chomado(0.731) | privatenumber(0.766) | SakanaAI(0.776) |
| 5 | Jannchie(0.730) | Desgard(0.766) | marcan(0.774) |

### 決定的所見

1. **3モデル共通の頑健な上位:** hoochanlon / SakanaAI / privatenumber
2. **Qwen3 ∩ Nomic = 7人（同型）、EmbGemma だけ別集合を拾う**
3. **limiting_axis がモデルで反転:**
   - EmbGemma → 全件 "b"（必要像→現状がボトルネック）
   - Nomic → 全件 "a"（意志↔意志の対称がボトルネック）
4. **スコアが0.72〜0.79に圧縮**（プール均質問題・全員エンジニア）
5. **スコア絶対値はモデル間で比較不可**（cos分布が全く異なる）

---

## 4. 未完了タスク（優先度順）

### 次にやること
- [ ] **1,090件プールで3モデル solo_eval 再実行**（均質性が崩れるか検証）
- [ ] **KHによる人手評価**：3モデルの上位を「会いたい順」と照合 → 採否判断の軸
- [ ] **Qwen3 を attribution 付きで再実行**（第1回は手動再構成で欠落）

### 中期
- [ ] 採否判断（§6-3）：順位妥当性 × チャネル健全性 × 速度/コストで決定
- [ ] 候補ベクトルのキャッシュ保存（1,090件×毎回計算は重い→後述スケール問題）
- [ ] eval_pairs_v1 の6seekerで pool_eval 再実行（1,090件プール）

### solo_eval 実行手順（PCでの操作）

```powershell
# 1. git pull で最新スクリプトを取得
git pull origin exp/embedding-model-eval

# 2. Qwen3
.\qwen3_server\run_qwen3.bat     # 別ウィンドウでサーバー起動
$env:POX_EMBED_MODEL_TAG = "qwen3-embedding-0.6b-d1024"
$env:POX_EMBED_BACKEND   = "qwen3"
$env:POX_QWEN3_ENDPOINT  = "http://localhost:8000/embed"
$env:SOLO_EVAL_TOP_N     = "30"  # 1090件なら30件くらい見たい
python eval/scripts/solo_eval.py
# → eval/snapshots/ に自動保存

# 3. EmbGemma（Qwen3サーバーを停止してから）
.\embgemma_server\run_embgemma.bat
$env:POX_EMBED_MODEL_TAG   = "embgemma-300m"
$env:POX_EMBED_BACKEND     = "embgemma"
$env:POX_EMBGEMMA_ENDPOINT = "http://localhost:8001/embed"
python eval/scripts/solo_eval.py

# 4. Nomic（EmbGemmaサーバーを停止してから）
.\nomic_server\run_nomic.bat
$env:POX_EMBED_MODEL_TAG = "nomic-emb-v2"
$env:POX_EMBED_BACKEND   = "nomic"
$env:POX_NOMIC_ENDPOINT  = "http://localhost:8002/embed"
python eval/scripts/solo_eval.py
```

---

## 5. スケール上の問題（考察）

現在の実装は「90件プールで動作確認」を前提に設計されており、プールが1,090件・本番では数万件になると複数の問題が顕在化する。

### 問題① 候補ベクトルの毎回再計算

**現状:** `solo_eval.py` / `pool_eval.py` はプール全件を実行のたびにベクトル化する。

| プール件数 | 所要時間（CPU推定） |
|---|---|
| 90件（第1回） | 数分 |
| 1,090件（今） | 20〜40分 |
| 10,000件（本番想定） | 数時間〜 |

**根本原因:** `build_vectors()` の結果をメモリ内でしか持たず、ファイルに保存しない。

**対策案:**
- `eval/cache/{MODEL_TAG}_vectors.npz` にベクトルを保存
- 次回実行時は jsonl の更新日時と比較し、差分のみ再計算（インクリメンタル更新）
- `numpy.savez_compressed` で十分（1,090件×4チャネル×1024次元 ≒ 数十MB）

---

### 問題② GitHub = エンジニア偏重プールの構造的限界

**現状:** 1,090件に拡張したが、全員「GitHubアカウントを持つ技術者」。PoXが本来つなぎたい層（NPO・行政・教育・福祉・アート・市民活動家）がほぼいない。

**影響:**
- スコアが0.7台に圧縮され続ける（全員が「作る人」として共通点を持つ）
- 非エンジニアのKH（または非エンジニアseeker）には機能しない
- 採用モデルを決めても、本番プールが均質なら意味が薄い

**対策案（優先度順）:**
1. **手動登録枠を設ける**：PoXのコンセプトに共鳴する非エンジニアを5〜10人手動で `github_profiles.jsonl` 形式で追加し、スコア分離が起きるか検証
2. **Twitter/X API**：bio に「教育」「NPO」「市民活動」等を持つアカウント収集（ただしAPIコスト高）
3. **LinkedIn API**：非エンジニア層が最も豊富だが、公式APIの制限が厳しく現実的でない
4. **PoX 登録者自身がプールになる**：本番デプロイ後は登録者が候補になるため、GitHub プールは「テスト用近似」と割り切る

---

### 問題③ ②生成（必要像生成）のコスト

**現状:** seeker 1人あたり Claude Opus を1回呼ぶ（`generate_necessity`）。`my_profile.json` に `derived_necessity` を紐づけることで再実行はスキップできる。

**本番スケール時の問題:**
- 新規seeker登録ごとにOpus呼び出し → 登録者が増えるとコスト増
- 登録フロー（register.html）でリアルタイム呼び出しすると UX が遅い（数秒〜十数秒）

**対策案:**
- **非同期生成**：登録完了 → バックグラウンドで②生成 → 完了まで「マッチング準備中」表示
- **モデルを Haiku に落とす（実験的）**：①生成の品質が落ちるが、速度・コストが改善。採否判断には実測が必要
- **キャッシュ**：同じ `will_text` + `state_*` から同じ `necessity_text` が生成されるため、ハッシュキーでキャッシュ可能（ただし意志文は個人ごとに一意のためヒット率低）

---

### 問題④ 1seekerに対して全候補をスキャンするO(N)計算

**現状:** `rank_candidates()` は全候補のベクトルと seeker ベクトルを総当たりでスコアリング。

| プール件数 | 計算量 |
|---|---|
| 1,090件 | 問題なし（ミリ秒〜秒） |
| 10,000件 | 数秒〜（CPU依存） |
| 100,000件 | 数十秒〜（実用限界）|

**対策案:**
- **shortlist化（match_config.py の SHORTLIST_K）**：現状 `SHORTLIST_K=50` の設定が `match_config.py` にあるが、実装が `rank_candidates` に組み込まれているか要確認
- **ANN（近似最近傍探索）**：`faiss` / `hnswlib` などで cos類似度の上位N件を高速取得し、精密スコアリングは上位のみに適用（二段階絞り込み）
- **ベクトルDBへの移行**：pgvector（PostgreSQL拡張）が既存DBと統合しやすく、Render 環境とも相性良い

### スケール戦略の発火順（2026-07-09 実験クローズ時に確定・段階発火）

母数 N の増加に応じて段階的に発火させる。各段は前段が限界に達したときのみ着手する（早すぎる最適化を避ける）。

1. **現状維持：フル768次元・総当たり**（N〜10⁵）。採用 nomic-emb-v2 のフル次元で全候補スコアリング。追加実装なし。
2. **int8 量子化**（メモリ圧が出たら）。※ **MRL256の失敗は量子化の失敗を予測しない**（別現象）ため、**量子化は別途検証が必要**。
3. **10⁵超：GPU 総当たり＋ハード要件 prefilter**（地域・言語・所属などの決定的条件で母集団を機械的に絞ってからスコアリング）。
4. **10⁶超：retrieve-then-rerank**。支配チャネルの ANN → 寛容な shortlist → 合成式で厳密再ランク。
   **リコールは「合成 TOP-k を正解」とみなして較正**する（ANN段の取りこぼしを合成スコア基準で測る）。
5. **密度機能（分布ベースの足切り・shortlist）は索引成立後**。前提: b チャネルの幾何・合成式のテール質量・
   絶対値でなく相対値での判断・**実成果ループ（接続成否ラベル）が必須**。索引が無い段階では蓄積のみ（judgment は将来）。

**検証前提（各段共通）:** 合成式の単調性確認（べき平均が入力に対し単調か）・律速軸分布の監視（全件単一軸への退化検出）。
**MRL 装置修正（256用再較正）は優先度引き下げ**（フル768で当面回すため）。

---

## 6. セキュリティ制約（絶対厳守）

- `ANTHROPIC_API_KEY` / `GITHUB_TOKEN` / `DATABASE_URL` はコードにもリポジトリにも書かない
- `eval/my_profile.json`・`eval/github_profiles.jsonl` は gitignore 済み（個人情報）
- `seeker` カラムは viewer/public API に絶対出さない
- `GET /profile/<id>` は必ず `get_profile_view()` のみ呼ぶ

---

## 7. 実験ルール（禁止事項）

- 異なる model_tag を同一計算で混ぜない
- `src/matcher_v4.py` を触らない
- `src/match_config.py` のパラメータをモデルごとに変えない
- raw テキストをベクトル化に渡さない（redacted のみ）
- 人間が判断すべき箇所を勝手に埋めない（match/unrelated ラベル、実出力次元・prefix 書式）

---

## 8. 環境変数まとめ

| 変数 | 役割 | 既定値 |
|---|---|---|
| `POX_EMBED_MODEL_TAG` | 使用モデル | `qwen3-embedding-0.6b-d1024` |
| `POX_EMBED_BACKEND` | バックエンド | `stub` |
| `POX_QWEN3_ENDPOINT` | Qwen3サーバーURL | `""` |
| `POX_EMBGEMMA_ENDPOINT` | EmbGemmaサーバーURL | `""` |
| `POX_NOMIC_ENDPOINT` | NomicサーバーURL | `""` |
| `POX_EMBED_FULL_DIM` | 次元数（override） | MODEL_DIMSから自動 |
| `ANTHROPIC_API_KEY` | Claude API | — |
| `GITHUB_TOKEN` | GitHub API | — |
| `GITHUB_PROFILE_COUNT` | 収集目標件数 | `100` |
| `GITHUB_QUERY_SET` | クエリ集合 japan\|global\|all | `japan` |
| `GITHUB_SEARCH_MAX_PAGES` | 1クエリあたりページ数 | `5` |
| `GITHUB_OVERSAMPLE` | 過剰収集倍率 | `2.5` |
| `SOLO_EVAL_TOP_N` | solo_eval の表示件数 | `10` |
| `POOL_EVAL_TOP_N` | pool_eval の表示件数 | `5` |
