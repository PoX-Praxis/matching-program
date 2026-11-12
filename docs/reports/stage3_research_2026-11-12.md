# 段階3 の調べもの（報告）— リソース不足の記録・nomic・候補の現物

- 依頼: `docs/handoff/PoX_段階3_LLMの選定_決まったことと決まっていないこと_2026-11-12.md` §4
- 日付: 2026-11-12
- 書いた人: Claude Code
- コードは変えていない。候補の確認は Hugging Face の公式のページ（API・モデルカード）と、llama.cpp の公式のソースで行った
- 本人の文・秘密は含まない

---

## 1. 前回のリソース不足の記録（66C・69）

**どちらも KAORU の PC ではなく、Claude Code のクラウドの作業環境で動かした。** その環境は CPU 4 コア・メモリ 15GB・GPU なし。

| 時 | 何を | 方式 | 足りなかったもの | かかった時間 | 使ったメモリ |
|---|---|---|---|---|---|
| 2026-11-07（66C） | L の判定（YES／NO の logit の差。生成しない） | transformers・fp32・CPU。モデル `llm-jp/llm-jp-3-1.8b-instruct`（rev `413b7929…`）、参考に `Qwen/Qwen3-1.7B` | **メモリ:** 4B 以上は fp32 で載らない。**時間:** bf16 は fp32 の約 3 倍遅い | 1.8B 級・fp32 で 1 対 約 1.4〜1.8 秒 | 記録無し（「載らない」だけ） |
| 2026-11-10（69） | 説明の生成（段1・段2） | transformers の素の生成（文法の縛りなし）・fp32・貪欲・seed 0・CPU 4 コア。同じモデル | 記録上は「足りない」とは書いていない。遅かった | 計測の 6 向きで 988 秒（1 向き約 2.7 分） | 記録無し |

- 出どころ: `scripts/probe_data/registration_v2.md` §8（66C の事前登録）・`docs/reports/69.md` §3・`local/69/explanation_measure_local.json`（手元）
- 1.8B の失敗の内訳: 内訳が分かる 1 回は「JSON は読めたが組が 0」。計測の 6 回は (a)・(b) を分けて記録していない（会話で報告済み）

## 2. nomic の動かし方の記録

| 項目 | 記録 |
|---|---|
| PC の性能 | 「自宅 Windows PC」「8GB 機」だけ。**GPU・CPU・ディスク・機種は無い**。記録の上では PC が 2 台ありうる（実験の PC と、常時動かしている PC） |
| 推論の方式 | sentence-transformers（4.0 以上）＋ torch（CPU 版）。fp32。`trust_remote_code=True`（nomic のモデルが要求する） |
| 常駐 | waitress を `127.0.0.1:8002` で起動し、NSSM の Windows サービス `PoXNomic` にした。スリープ・休止を切った |
| 本番からのつなぎ方 | Cloudflare Tunnel（ダッシュボードで作る固定 URL・cloudflared もサービス）。合言葉は `X-API-Key`。ルーターのポートは開けていない |
| 速さ | 実験（2026-07）の初回: 候補 1,090 人 × 4 ベクトル＝2025.6 秒（1 ベクトル約 0.46 秒。途中で 4 回落ちて再開した通算の可能性がある） |
| 版の固定 | ライブラリは下限だけ。モデルの revision も固定していない |

- 出どころ: `docs/deploy_local_nomic.md`・`docs/HANDOVER_2026-07_nomic_v4.md`・実験ブランチ `exp/embedding-model-eval` の `eval/log.md`

## 3. 候補の現物（2026-11-12 に確認）

### 3-1. 一覧

| 候補 | 公式のリポジトリ（revision） | ライセンス | 指示応答の版 | llama.cpp（公式） | GGUF（4bit／8bit のファイルの大きさ） |
|---|---|---|---|---|---|
| gpt-oss-20b | `openai/gpt-oss-20b`（`6cee5e81…`） | Apache-2.0 | **無い。** 推論（reasoning）のモデルで、強さを low／medium／high から選ぶ（切れない）。harmony の形式でしか使えない | 対応（アーキテクチャ `gpt-oss`） | 公式（ggml-org）: **MXFP4 12.11 GB** だけ。重みがはじめから MXFP4 のため、Q4／Q8 の別のファイルは無い。カードには「16GB のメモリで動く」 |
| Nemotron Nano 9B v2 Japanese | `nvidia/NVIDIA-Nemotron-Nano-9B-v2-Japanese`（`3979dd16…`） | **NVIDIA Nemotron Open Model License**（2025-12-15 版）。3-2 を見る | 一つのモデルで、推論を切れる（`enable_thinking=False`。そのときは貪欲を推奨） | 対応（アーキテクチャ `nemotron_h`。Mamba と Transformer の混在。公式のソースにある） | **公式（NVIDIA）の GGUF は無い。** 第三者（mradermacher ほか）: Q4_K_M **6.53 GB**／Q8_0 **9.46 GB** |
| LLM-jp-4 8B（指示応答） | `llm-jp/llm-jp-4-8b-instruct`（`098f2b2c…`） | Apache-2.0 | ある（**4.0 の SFT だけ**。DPO なし）。4.1 に指示応答の版は無い（thinking だけ） | 対応（アーキテクチャ `llama`）。4.1 の GGUF は llama.cpp の公式の安定版 **v0.6.0 以上**で動く（公式の cookbook） | **公式の GGUF は無い**（公式は thinking の版だけ: 4.1-8b-thinking Q4_K_M 5.5 GB）。第三者（mmnga-o）: Q4_K_M **5.3 GB**／Q8_0 **9.13 GB** |
| LLM-jp-4 32B-A3B | `llm-jp/llm-jp-4-32b-a3b-thinking`（`30b90b28…`）・4.1 は `llm-jp/llm-jp-4.1-32b-a3b-thinking` | Apache-2.0 | **無い**（base と thinking だけ） | 対応（アーキテクチャ `qwen3moe`） | 公式（4.1-thinking）: Q4_K_M **21.52 GB**・BF16 64.3 GB。Q8 は無い |

- revision は、確認した時点の main の commit
- LLM-jp-4 33B（dense）は Apache-2.0。今回の候補の表には無いので、細かくは見ていない

### 3-2. Nemotron のライセンスの版と、安全の仕組みの条項

ライセンスが 2 つある。

| モデル | ライセンス | 安全の仕組み（guardrail）の条項 |
|---|---|---|
| **Nemotron Nano 9B v2 Japanese**（候補） | NVIDIA Nemotron Open Model License（v. 2025-12-15） | **無い。** 権利が自動で失われるのは、モデルや出力について特許・著作権の訴えを起こしたときだけ。商用可。出力の所有は主張しない |
| Nemotron Nano 9B v2（英語の元のモデル） | NVIDIA Open Model License Agreement（v. 2025-10-24） | **ある。** 安全の仕組みを外したり弱めたりすると、同等の仕組みを置かない限り、権利が自動で失われる |

- 第三者の GGUF の一つ（mmnga-o）は、カードに古いほうのライセンス名を書いている。使うときは、公式のリポジトリのライセンスを正とする
- 配るときの決まり: ライセンスの写しを渡す。NOTICE ファイルがあれば帰属の文を入れる。公式のリポジトリに NOTICE ファイルは無い。PoX は配らないので、関係するのは手元に保存する写しだけ

### 3-3. `trust_remote_code` と llama.cpp

- **llama.cpp（GGUF）の道では、どの候補も Python のリモートコードを使わない。** トークナイザは GGUF の中に入っている
- transformers の道で使う場合:
  - LLM-jp-4 はトークナイザが独自（`auto_map` の `Llmjp4Tokenizer`）。Nemotron はモデルの定義が独自（`modeling_nemotron_h.py`）
  - どちらも `trust_remote_code=True` が要る。決定 #8（使わない）なら、GGUF の道だけになる
- gpt-oss-20b にはリモートコードが無い
- 報道にあった「LLM-jp-4 は専用の fork が要る」について:
  - 公式の cookbook は「公式の llama.cpp の v0.6.0 以上」としていて、fork の記述は無い
  - 4.0 の GGUF は、チャットの型の宣言が足りないので、4.1 の GGUF を勧めている

## 4. 推測で補完した箇所

- **メモリの目安（推測）:** ファイルの大きさから見ると、手元で動かすには次の量が要りそう。KV キャッシュなどで、実際はファイルより 1〜3GB ほど多く使う見込み
  - 4bit の Nemotron（6.5 GB）と LLM-jp-4 8B（5.3 GB）は 8GB 前後
  - gpt-oss-20b（12.1 GB）は 16GB 前後
  - 32B-A3B（21.5 GB）は 24GB 以上
- **v0.6.0 の版の名前:** llama.cpp の版の名前（v0.6.0）は、公式の cookbook の記述による。リリースのページを、この環境から開いて確かめることはできなかった
- **第三者の GGUF の中身:** 公式の重みから変換したものかどうかは、カードの記述だけで、確かめていない

## 5. 段階3a の指示書を書くのに足りない情報

1. **KAORU の PC の性能**（GPU・VRAM・主メモリ・CPU・ディスク。どの PC か）→ #184
2. **「thinking の版を避ける」（決定 #8）と候補が合わない**
   - gpt-oss-20b と LLM-jp-4 32B-A3B には、指示応答の版が無い
   - 推論を最も弱く／切って使うのを認めるか、候補から外すかを決める
   - Nemotron は推論を切れる。LLM-jp-4 8B は指示応答の版がある
3. **公式の GGUF が無い候補（Nemotron・LLM-jp-4 8B instruct）の扱い**
   - 案 1: 公式の safetensors から自分で変換する。LLM-jp-4 は独自トークナイザなので、変換できるかは未確認
   - 案 2: 第三者の GGUF を、ハッシュを記録して使う
   - 版の保存（決定 #7）と合わせて決める
4. **文法の縛り（GBNF）と、harmony・推論の出力の形が両立するか**
   - gpt-oss と LLM-jp-4 は harmony、Nemotron は `<think>`
   - 推論の部分を縛りの外に置く方法を、試して決める
5. **llama.cpp の版と組み方**（CPU だけか、CUDA か）→ PC の性能しだい
