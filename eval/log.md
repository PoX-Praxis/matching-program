# PoX v4 マッチング評価ログ

実験結果・システム調整・検証メモをここに積み上げる。
新しいエントリは**上に追記**する（最新が先頭）。

---

## フォーマット

```
### YYYY-MM-DD｜[モデル名 or システム調整]｜[種別: 実験/調整/検証]
**内容:** 何をやったか
**結果:** 何が出たか
**気づき:** 何が分かったか
**次のアクション:** 何をすべきか
**スナップショット:** `snapshots/ファイル名.json`（あれば）
```

---

## 2026-07-08｜モデル選定 最終確定（nomic-emb-v2）｜決定

**MRL256 実測（`eval/analysis/mrl_report.md`）:** フル vs 先頭256次元の順位突合。
| モデル | TOP30 Jaccard | Spearman | TOP10残存 | H-3 |
|---|---|---|---|---|
| Qwen3 | 0.132 | 0.000 | 1/10 | 不合格（最大劣化）|
| EmbGemma | 0.463 | 0.377 | 3/10 | 不合格 |
| Nomic | 0.500 | 0.463 | 6/10 | 不合格（最小劣化）|
→ **3モデルとも H-3 不合格**。256 shortlist は不成立。劣化の小ささ Nomic＞EmbGemma＞Qwen3。
経路別でも b（必要像→現状）が全モデルで最も低次元化に弱い。

**決定（2026-07-08・決定者 KH）: 採用モデル `nomic-emb-v2` に確定。**
- 根拠：①人手評価=差なし僅差 → ②-2でEmbGemma脱落（Gemma独自ライセンス）→ 供給/監査/コストでNomic優位、
  ③MRLでもNomicが最小劣化で「256でQwen3昇格」の再協議条件は不発、④較正は全モデル差なし。
- 付随決定：**256次元shortlist構想は現状不成立、本番はフル768次元で設計**。中間次元(512/384)再測定は将来課題。

**成果物:** `eval/analysis/final_report.md`（§5実測更新・§6③行更新・§8決定追記）。コード変更なし。

---

## 2026-07-07｜全件スコア分布の蓄積＋MRL256検証基盤＋課題追記｜調整

**タスク1（分布蓄積・恒久機能）:** `eval/scripts/distribution_log.py` を追加、solo_eval にフック。
実行のたびに TOP30 snapshot とは別に、全候補の `(login, final_score, a_sim,b_sim,c_sim, limiting_axis)` を
`eval/analysis/distributions/{model_tag}_{ts}_full_scores.jsonl`＋necessity_id 単位の percentile/b近傍密度
`_summary.json` に蓄積（gitignore・**判定ロジックは作らず蓄積のみ**）。蓄積単位＝`necessity_id`
（必要像 sha256 先頭12桁）。同一必要像の蓄積回数を stdout 表示。snapshot スキーマは不変。

**タスク2（MRL256 検証・基盤）:** `eval/scripts/mrl_report.py` を追加。**キャッシュ済みベクトルのみ**で
フル次元 vs 先頭256次元（キャッシュ _256＝スライス＋L2再正規化済）のランキングを突合
（TOP30 Jaccard/Spearman・TOP10残存・limiting_axis一致率・経路a/b/c別）。H-3合否（Jaccard≥0.9 かつ
TOP10残存≥9）を判定。**サーバー不要**。ただし seeker ベクトルが従来未保存だったため、solo_eval に
seeker full+256 の永続化（`eval/cache/seeker_{tag}_{nid}.json`）を追加して MRL をオフライン化。
→ **実数値は PC 実行（cache＋seekerベクトルが PC 側）。** cloud では stub 機構検証のみ。

**タスク3（課題追記）:** `spec/matching_spec_v0.6.md` §12.A に「必要像の複数仮説化（multi-necessity）」を追記
（平均化の病理／仮説別分割生成／necessity_id 単位で互換／未着手・構想）。

**テスト:** 既存140＋新規7（distribution_log 5・mrl_report 2）＝**147 全通過**。matcher_v4/match_config/necessity_gen 不変。

**次のアクション:** PC で solo_eval を各モデル1回再実行（seekerベクトル捕捉＋分布蓄積）→ `python eval/scripts/mrl_report.py` で MRL256 実数値を取得。

---

## 2026-07-05｜モデル選定 最終レポート作成｜検証

**内容:** 決定ルール（①人手評価→②ライセンス/説明可能性→③MRL→④較正）に沿って全材料を `eval/analysis/final_report.md`（gitignore）に集約。結論（採用モデル）は書かず判断材料の整理まで。

**各基準の要点:**
- ①人手評価：**差なし〜僅差**。会いたい度が3〜4に圧縮（5・1〜2が0）、平均差最大0.10、指標ごとに勝者交代（平均/取りこぼし=Qwen3、順位一致=EmbGemma、中間=Nomic）。「現プールに"ぜひ会いたい(5)"不在」という事実。
- ②-1 説明可能性：limiting_axis は全モデル単一軸（Q/N=a・E=b、90件時の反転が維持）＝説明は単調。優劣なし・視点の違いのみ。
- ②-2 ライセンス：Qwen3=Apache2.0・Nomic=Apache2.0（学習データ公開）・**EmbGemma=Gemma独自（gated・使用制限伝播義務）**。ここに差。
- ③MRL256：**未実施**（判定材料なし）。キャッシュ済み full/short から後日計算可。
- ④較正：`test_calibration_v4.py` を3モデルのMODEL_TAGで実行 → **全モデル 12/12 passed**（差なし）。

**副産物（確定）:** 候補necessityは照合不参照（matcher_v4 L55-57）／prefixキー修正／attribution欠落は再実行で解消／キャッシュ＋クラッシュ耐性で 34〜40分→1.35秒／全140テスト通過。

**次のアクション（最終決定はユーザー）:**
- [ ] マトリクスを見て採用モデルを1本決める（①僅差 → ②-2ライセンスの比重が上がる状況）
- [ ] 決めきれない/③を見たい場合：Nomic等でMRL256劣化をキャッシュから計算
- [ ] 非エンジニア拡充（"5"が付く母集団づくり）は別トラック

---

## 2026-07-05｜3モデル分析データ出力（人手評価の準備）｜検証

**内容:** `eval/scripts/analyze_3models.py` で1,090件×3モデルのスナップショットを分析し、`eval/analysis/`（gitignore・個人データ本文を含むため非コミット）に6成果物を生成：`comparison_report.md` / `human_eval_sheet.md`+`.csv` / `timing_report.md` / `blind_eval_sheet.md`+`.csv` / `blind_key.json`。ブラインド突合用に `eval/scripts/score_blind_eval.py` も追加。

**構造比較の数値（TOP30）:**

| ペア | TOP30 Jaccard | Spearman ρ（共通候補）|
|---|---|---|
| Qwen3∩EmbGemma | 0.364（共通16）| 0.268（n=16）|
| Qwen3∩Nomic | 0.333（共通15）| 0.107（n=15）|
| EmbGemma∩Nomic | 0.277（共通13）| 0.297（n=13）|

- limiting_axis（TOP30）：Qwen3=30a / EmbGemma=30b / Nomic=30a
- スコア幅（TOP30）：Qwen3 0.0342 / EmbGemma 0.0334 / Nomic 0.0331（絶対値のモデル間比較はしない）
- 3モデル共通：TOP10=2件・TOP30=10件 ／ 固有候補：Qwen3 9・EmbGemma 11・Nomic 12
- ブラインド和集合：56 行（seed=20260705）

**90件時の3知見の再現判定:**
1. **共通上位層の存在** → **再現**。3モデル共通が TOP30 で10件・TOP10で2件。頑健な上位層はある（ただし顔ぶれは多様化で hoochanlon/SakanaAI 系 → sujii/woxtu 系に交代）。
2. **「Qwen3≒Nomic・EmbGemma だけ別」** → **変化（明確には再現せず）**。limiting_axis のクラスタ（Q,N=a／E=b）は再現するが、**順位相関では Qwen3∩Nomic が最弱（ρ=0.107）**。membership も rank も Qwen3 が中心的で、90件時の「Q≒N が最も似る」は1,090件では成立しない。→ 母数を10倍にすると細かな順位は各モデル独立に近い。
3. **limiting_axis 反転（EmbGemma=b・Nomic=a）** → **再現**（E=30b, N=30a, Q=30a）。

**気づき:** 90件時の「Q≒N」は overlap のカウントに引っ張られた見え方で、順位相関で見ると1,090件では崩れる。**モデルは互いにかなり独立に並べている**（弱いρ）。だからこそ最終判断は数値でなく **KHの会いたい順との一致（ブラインド評価）** で決める必要がある。

**次のアクション:**
- [ ] KH が `eval/analysis/blind_eval_sheet.md`（またはcsv）を記入 → `score_blind_eval.py` で突合 → モデル別の会いたい度・順位相関を得る
- [ ] その後 `human_eval_sheet.md` で「なぜ納得か」を言語化
- [ ] 決定ルール（人手評価→僅差ならライセンス/説明可能性→MRL→較正テスト）で1本確定

---

## 2026-07-05｜3モデル比較（KH solo eval・1,090件プール）｜検証

**内容:** 拡張プール1,090件（日本590＋海外500）で、Qwen3 / EmbGemma / Nomic を同一 seeker(KH)・同一パラメータ（gamma=0.43, p=-0.3, alpha=1.0, beta=1.2）・必要像は claude-opus-4-8 事前生成の共通値でマッチング。TOP30。

**スナップショット（すべて attribution・timing 付き）:**
- `snapshots/solo_KH_qwen3-embedding-0.6b-d1024_20260705_092936.json`
- `snapshots/solo_KH_embgemma-300m_20260705_174952.json`
- `snapshots/solo_KH_nomic-emb-v2_20260705_222110.json`

### 効率化の成果（ベクトルキャッシュ＝フェーズ2-3で実装）

| モデル | 1回目 候補ベクトル | 2回目（キャッシュ後）| 倍率 |
|---|---|---|---|
| EmbGemma | 2383.5秒 | 1.35秒 | ~1765× |
| Nomic | 2025.6秒 | 3.05秒 | ~664× |
| Qwen3 | 20266秒（PCスリープ込み）| 数秒 | — |

- `eval/scripts/vector_cache.py`：モデル別 `.npz`・入力テキストsha256キー・model_tag不一致で例外。
- クラッシュ耐性：25件ごとチェックポイント保存＋原子的書き込み。**Nomic 1回目は4回クラッシュしたが毎回続きから再開して完走**（875件保存済みから+215で1090）。

### 上位10（1,090件）

| # | Qwen3 | EmbGemma | Nomic |
|---|---|---|---|
| 1 | furukawa1020 | hfu | **sujii** |
| 2 | **sujii** | **sujii** | Cj-bc |
| 3 | woxtu | woxtu | hfu |
| 4 | behnamazimi | danzeeeman | tuanchauict |
| 5 | Misaki0331 | channingallen | LumaKernel |
| 6 | radioblahaj | higedamc | behnamazimi |
| 7 | tuanchauict | Cj-bc | hoochanlon |
| 8 | channingallen | adexot | woxtu |
| 9 | dwango | furukawa1020 | jspolsky |
| 10 | gil-- | izelnakri | es |

### 構造（90件版と同じ骨格が維持）

- **limiting_axis**：Qwen3=全件 a ／ EmbGemma=全件 b ／ Nomic=全件 a（90件版と同じ反転構造）
- **Qwen3 ∩ Nomic = 9/15（top15）** で最も似る。EmbGemma は各モデルと ∩5 で**外れ値**。→ a制約組(Q,N)と b制約組(E)という2クラスタ。
- **3モデル共通（top15）= @sujii / @woxtu / @Cj-bc**
- スコア幅は3モデルとも約0.033（絶対値はモデル間比較不可・順位のみ比較）

### 各候補の3モデル順位（抜粋・-=TOP30圏外）

| login | Qwen3 | EmbGemma | Nomic | 備考 |
|---|---|---|---|---|
| @sujii | 2 | 2 | 1 | 意味と構造・共鳴・自己調整アーキ＝KH必要像と深く一致。**全モデル最上位** |
| @woxtu | 3 | 3 | 8 | 全モデル top8 |
| @Cj-bc | 11 | 7 | 2 | 全モデル共通 |
| @hfu | 24 | 1 | 3 | E/N の champion（誰もが空間情報インフラを所有できる世界）|
| @furukawa1020 | 1 | 9 | 19 | Qwen3 の champion（誰もが生きていてよかったと思える瞬間）|
| @tuanchauict | 7 | 20 | 4 | Q/N 寄り |

**気づき:**
- **プール多様化が効いた**：90件版のトップ（hoochanlon/SakanaAI＝汎用エンジニア）が押しのけられ、**意志がKHのビジョンに共鳴する人**（sujii/hfu/furukawa1020/woxtu/Cj-bc）が上位化。
- **@sujii が3モデル独立に最上位**＝母集団・モデルに依らない頑健な一致。最有力候補。
- モデルの個性：EmbGemma/Nomic は @hfu を、Qwen3 は @furukawa1020 を強く推す。EmbGemma は「必要像→現状(b)」が律速＝現状の具体性を重視、Qwen3/Nomic は「意志↔意志(a)」が律速。

**次のアクション:**
- [ ] **KH本人の人手評価**：上位（特に sujii/hfu/furukawa1020/Cj-bc/woxtu）に「実際に会いたいか」を照合 → どのモデルの順位が最も納得できるか
- [ ] 採否判断（設計書 §6-3）：順位妥当性 × チャネル健全性 × 速度/コスト
- [ ] 必要なら非エンジニア（NPO/行政/福祉）をさらに追加して母集団の偏りを是正

---

## 2026-06-26｜プール拡張 90 → 1,090 件｜調整

**内容:** 均質プール問題（全員エンジニア・スコア圧縮）の対策として、GitHub プロフィールプールを拡張。`fetch_github_profiles.py` を改良し2パス実行。

**改良点（コミット 3529841 / 66137b4）:**
- フォロワーを range で帯域分割（トップ有名人の再取得回避 → 新規が増える）
- 役割/ドメイン軸クエリ追加（研究・デザイン・社会課題・教育・公益）で均質性を緩和
- bio/repos フィルタ脱落を見込んだ過剰収集（`GITHUB_OVERSAMPLE`=2.5）
- 既処理ログインの自動スキップ（レジューム）
- `GITHUB_QUERY_SET=japan|global|all` で日本／海外を切替

**実行結果:**

| パス | 新規処理 | スキップ | 累計 |
|---|---|---|---|
| 初回（日本中心） | — | — | 90 |
| japan（追加500） | 500 | — | 590 |
| global（追加500） | 500 | 385 | **1,090** |

- global は候補1,348件収集 → 500件構造化（skip 385：bio短/repos少）
- 例: ericwindmill(Flutter), sblackshear(Move言語), MMBazel(MLOps), ロボティクス・クリエイティブコーディング・デザイン×技術系 → 国際的かつ職種が従来より多様

**重要な設計判断:**
- 海外アカウントの英語 bio も**構造化層が日本語へ正規化**（STRUCTURE_PROMPT）→ プールは日本語で統一。seeker(KH) の英語化は不要（日英をまたがず、モデル比較に言語の交絡を持ち込まない）。

**データの所在:**
- `eval/github_profiles.jsonl`（1,090行）は **gitignore・PCローカルのみ**（個人情報のため非コミット）。git には上げない。再生成はスクリプト再実行で可能。

**次のアクション:**
- [ ] 拡張プール1,090件で solo_eval を3モデル再実行（Qwen3 / EmbGemma / Nomic）
- [ ] 均質性が崩れ順位が分離するか・スコアレンジが広がるか検証（第1回90件と比較）
- [ ] Qwen3 を attribution 付きで実行（第1回は手動再構成で欠落）

---

## 2026-06-26｜3モデル比較（KH solo eval 第1回）｜検証

**内容:** KH（seeker）固定・必要像は claude-opus-4-8 事前生成を3モデル共通使用・GitHub プール90件・パラメータ固定（gamma=0.43, p=-0.3, alpha=1.0, beta=1.2）で、Qwen3 / EmbGemma / Nomic の3モデルを同一コードでマッチング。

**スナップショット:**
- `snapshots/solo_KH_qwen3-embedding-0.6b-d1024_20260625.json`
- `snapshots/solo_KH_embgemma-300m_20260626_022326.json`
- `snapshots/solo_KH_nomic-emb-v2_20260626_022645.json`

### 上位10件の並び

| Rank | Qwen3 | score | EmbGemma | score | Nomic | score |
|---|---|---|---|---|---|---|
| 1 | hoochanlon | 0.7460 | SakanaAI | 0.7754 | hoochanlon | 0.7919 |
| 2 | SakanaAI | 0.7394 | hoochanlon | 0.7724 | yuk7 | 0.7816 |
| 3 | yusukebe | 0.7346 | Jannchie | 0.7697 | privatenumber | 0.7810 |
| 4 | chomado | 0.7307 | privatenumber | 0.7663 | SakanaAI | 0.7756 |
| 5 | Jannchie | 0.7297 | Desgard | 0.7659 | marcan | 0.7738 |
| 6 | marcan | 0.7275 | matchai | 0.7624 | gfx | 0.7709 |
| 7 | onevcat | 0.7257 | sxzz | 0.7621 | brettwooldridge | 0.7698 |
| 8 | kana | 0.7253 | unhappychoice | 0.7607 | kana | 0.7688 |
| 9 | privatenumber | 0.7243 | kallydev | 0.7606 | yusukebe | 0.7656 |
| 10 | gfx | 0.7218 | kevinzhow | 0.7573 | Santos-Enoque | 0.7656 |

### 一致構造

- **3モデル共通（top10）**: hoochanlon / SakanaAI / privatenumber → 3人
- **Qwen3 ∩ Nomic = 7人**（ほぼ同型）／ Qwen3 ∩ EmbGemma = 4人 ／ EmbGemma ∩ Nomic = 3人
- **EmbGemma が外れ値**：Web3（Desgard）・OSSメンテナ（matchai/sxzz/kallydev）系を独自に上位化
- **hoochanlon は全モデル Rank1〜2** の頑健なトップ、SakanaAI も Rank1〜4 で安定

### 決定的所見：limiting_axis がモデルで反転

| | a（意志↔意志 対称） | b（必要像→現状） | c（意志 passage） | limiting_axis |
|---|---|---|---|---|
| EmbGemma | 0.61〜0.67（高） | 0.41〜0.46（低=制約） | 0.47〜0.56 | 全件 "b" |
| Nomic | 0.48〜0.54（低=制約） | 0.51〜0.60 | 0.62〜0.68（高） | 全件 "a" |

- **EmbGemma**：意志はよく合う（a高）が「現状が必要像を満たすか」（b）が常にボトルネック＝KHのマッチの難所を b で捕捉
- **Nomic**：意志passage・現状（c/b）は出るが、意志↔意志の対称類似（a）が全体的に低くそこが制約
- → モデルごとに cos類似度の絶対水準・分布が全く異なる。パラメータ固定（公平比較のため正しい）の結果、**スコア絶対値はモデル間で直接比較不可。比較対象は順位構造のみ**。
- （Qwen3 は手動再構成スナップのため attribution なし。次回再実行で取得すること）

### スコア分布（均質プール問題は3モデル共通）

| | 最高 | 最低 | レンジ幅 |
|---|---|---|---|
| Qwen3 | 0.7460 | 0.7218 | 0.0242 |
| EmbGemma | 0.7754 | 0.7573 | 0.0181 |
| Nomic | 0.7919 | 0.7656 | 0.0263（分離度最大）|

**気づき:**
- 3モデルとも0.72〜0.79に圧縮 → プールが全員エンジニアで均質という根本問題は不変
- 「どのモデルが良いか」は**順位の妥当性をKHが人手で判定**するしかない（スコア絶対値では決められない）。hoochanlon/SakanaAI/privatenumber の3人がKHにとって本当に会いたい相手かが判断軸
- EmbGemma だけ別集合を出すのは採否判断の分岐点。EmbGemma の上位（unhappychoice「エンジニアが本当に価値あることに注力できる世界」、kallydev「人々が本当に求めるプロダクト」）は意志文がKHと語彙的に酷似 → 意志の意味照合は EmbGemma が鋭い可能性

**次のアクション:**
- [ ] KH が3モデルの上位を見て「会いたい順」と照合 → どのモデルの順位が一番納得できるか人手評価
- [ ] Qwen3 を attribution 付きで再実行（チャネル別比較を3モデル揃える）
- [ ] 採否判断（設計書 §6-3）：順位妥当性 × チャネル健全性 × 速度/コストで決定
- [ ] 非エンジニアをプールに混ぜて均質性を崩す（別タスク）

---

## 2026-06-25｜Qwen3 × KH solo eval｜実験

**内容:** KH（seeker）の必要像を claude-opus-4-8 で事前生成し、GitHub プロフィール 90 件（エンジニア中心）に対して Qwen3-Embedding-0.6B でマッチング実行。

**必要像（②生成）:**
> PoXによって『人々が本当にやりたいことに行動できる世界』を共に作りたいという意志を持ち、その構想を実際に動くシステムへと実装・検証できる人。抽象的な構造や前提を理解したうえで、接続プロセスをプロトタイプとして形にし、機能するまで作り切れる技術力と実行力がある。AIや既存技術を用いてプロダクトを立ち上げた経験を持ち、まだ解決策のない問題に対して自ら手を動かして解を生み出せる。同時に、環境や条件に縛られず根本から考える姿勢に共鳴できる存在。

**パラメータ:**
- `gate_s=0.8`（意志要求強い：相手にも PoX の意志を求める）
- `gate_u=0.3`（素材は厚いが実装段階に不確実性あり）
- `gamma=0.43`
- `p_sharpness=-0.3`（意志＋実装力の両方必須：ソフト AND）
- `alpha=1.0` / `beta=1.2`（補完をやや重視：実装力が足りない側）

**結果:** 上位 10 件
**スナップショット:** `snapshots/solo_KH_qwen3-embedding-0.6b-d1024_20260625.json`

| Rank | 名前 | login | score | 特徴 |
|---|---|---|---|---|
| 1 | 123456 | @hoochanlon | 0.7460 | ネットワーク・インフラ技術、実用ツール開発、自由なネット空間を大切に |
| 2 | Sakana AI | @SakanaAI | 0.7394 | LLMエージェント・マルチエージェントシステム・次世代知能システム |
| 3 | Yusuke Wada | @yusukebe | 0.7346 | Hono 作者、Cloudflare、「楽しさ重視で価値あるものを作り続けたい」|
| 4 | ちょまど | @chomado | 0.7307 | MS Cloud DA、開発者コミュニティ支援・発信力 |
| 5 | Jianqi Pan | @Jannchie | 0.7297 | ML実験管理・画像評価ツール、実用的で美しいツールを作る |
| 6 | Hector Martin | @marcan | 0.7275 | システム改善・深い技術理解、「壊れていないものをさらに良くしたい」|
| 7 | Wei Wang | @onevcat | 0.7257 | iOS/Swift OSS、マルチエージェントAI設計、クリエイター向けツール |
| 8 | Kana Natsuno | @kana | 0.7253 | 本質的なツール・自動化、「人間らしい創造活動を支援したい」|
| 9 | Hiroki Osame | @privatenumber | 0.7243 | JS/Node.js OSS、技術的ソリューションを世界と共有したい |
| 10 | FUJI Goro | @gfx | 0.7218 | ソフトウェアエンジニア、プログラミングで価値を創造 |

**気づき（pool_eval.py × 6 seeker 分の所見）:**
- AdiChat（@AdiChat）が c01〜c04 の 4 ケースで Rank 1 → プールが全員エンジニアのため、発信力の高い汎用的な人物が何にでも上位に出る
- スコアが全体的に 0.68〜0.77 に圧縮：母集団が均質すぎて見分けがつかない
- **根本原因：GitHub = 開発者のプラットフォーム。NPO・行政・教育・福祉など非エンジニアがいない**

**次のアクション:**
- [ ] solo_eval（KH 固定）の結果を KH 本人が妥当性確認
- [ ] EmbGemma / Nomic で同じ solo_eval を実行して 3 モデル比較
- [ ] 非エンジニアをプールに混ぜる方法を検討（Twitter API? LinkedIn? 手動登録?）

---

## 2026-06-25｜pool_eval.py キャッシュ最適化｜システム調整

**内容:** 候補ベクトルを seeker ごとに再計算していた（90件×6=540回）を、1回だけ計算してキャッシュするよう変更（90回）。

**変更ファイル:** `eval/scripts/pool_eval.py`
**効果:** 計算量を約 1/6 に削減。CPU 実行でも現実的な時間に短縮。

---

## 記録すべき項目の例

- モデルの変更（Qwen3 → EmbGemma → Nomic）
- パラメータの変更（gamma_max, alpha, beta, p）
- プロンプトの変更（必要像生成プロンプトのバージョン）
- プールの変更（追加・削除・多様化）
- 評価ケース（eval_pairs）の追加・修正
- デプロイ後の本番観察
