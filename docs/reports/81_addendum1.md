# 報告 81 追補1 — 走らせない決まり・画面のギャップを直す（順1）

ジョブは起動していない（費用 0）。順2 は KAORU の明示の許可を待つ。

## 1. 禁則抵触／推測で補った箇所

- 禁則抵触：なし。KAORU・PA の文はリポジトリ・PR・この報告に入れていない（§2 の比べは手元 `local/t81/diag/` で、数と位置だけを書く）。鍵は書いていない。表示のフラグは切ったまま
- 推測で補った箇所
  - §3-1 番号の形：`N01`・`O18`・`P012`・`C031`・`{O}18`（流れの直しで `O18` が `{O}18` になる形）を、括弧ごと・並び（`・`・`、`）ごと消す。2〜3 桁の数字が続くものだけ（`2024年`・`（3年）` は消さない）。消した後に残る空白・読点の重なりを詰める
  - §3-2 文の終わりの確かめは、流れのカードの ① の各文に当てる（v0.4 のカードは前の閉じた語彙のまま）
  - §3-2 ラベルの語の英語は、大文字・小文字を区別しない。足した語：同種・same_kind・relational・partial・same_interest・topic_only・irrelevant・unclear・resolves・eases・worsens・constrains
  - §3-3 「縛り（足りないもの）」は、部品の機械の分け方（`bound_sub`）で lack のもの。見出しの「〇〇さん」は縛りのある人、「△△さん」は相手。カードの目的の番号は、目的の欄が無い人の必要像（p1）の次（p2）
  - §3-3 スイッチを入れたときは、縛られているものをカードの引用（citations）に入れる（入れないと引用の確かめに落ちる）。切っているときのカードは前と同じ形
  - §3-4 選ぶのは、カードの確かめ（`X.check`）を通った ① がある自分の目的。いくつかあれば最初のもの

## 2. CLAUDE.md の文と決定ログの置き場所

- CLAUDE.md に「費用のかかる実行の決まり」の節を足した：「モデルを走らせるジョブ（HF Jobs など費用のかかるもの）は、KAORU が明示に許可したときだけ起動する。原因を確かめるための試しの実行もこれに含む。費用 0 の調べは自由。」
- 決定ログ 2026-11-24：**未受領**。リポジトリにも会話にも本文が無いため、`docs/decisions/` には置いていない（作文しない）。KAORU から本文が届いたら `docs/decisions/2026-11-24.md` に置く

## 3. §2 の比べ（費用 0）

### プロンプトの描き方（vLLM＝openai-harmony／llama.cpp＝b1b770d の jinja・GGUF に入った型）

| 項目 | 数 |
|---|---|
| 比べた入力（ラベル・選ぶの段。架空 18・KAORU×PA 6） | 24 |
| 描いた文が一字同じ | 24（違う位置：なし） |
| system の部分が同じ | 24 |
| トークン数が同じ | 24（1397〜7252） |
| 両方に `Reasoning: medium` | 24 |
| llama.cpp で推論の深さを渡さなくても同じ（型の既定が medium） | 24 |
| 81 順3 の記録のトークン数（N_by_O・O_by_N）＝ harmony で数えた数 | 4103・4543（同じ） |
| v06（vLLM）の記録のトークン数 ＝ harmony で数えた数 | 4 件とも同じ |

- GGUF に入った会話の型は、公式の chat_template.jinja と同じ（sha256 a4c9919c…）
- 結論：描いたプロンプトは違わない。v06 との違いの元はここではない

### 重みの形

| 部分 | 公式（openai/gpt-oss-120b） | 使っている GGUF（ggml-org・file_type 38） |
|---|---|---|
| 専門家（ffn_up/gate/down_exps） | MXFP4 | MXFP4（108） |
| 注意（attn_q/k/v/output） | BF16 | **Q8_0**（各 36） |
| 埋め込み（token_embd） | BF16 | **Q8_0** |
| 出力（output） | BF16 | **Q8_0** |
| ルーター・ノルム・バイアス・sinks | BF16 | F32（433） |

- BF16 から Q8_0 になったのは、注意 約 0.96B・埋め込みと出力 約 1.16B の重み
- （推測）llama.cpp の量子化した行列の掛け算は、入力の側も Q8_1 にする。vLLM は BF16 のまま

### 直す手の案（KAORU が決める。どれも走らせるには許可が要る）

1. 専門家だけ MXFP4、ほかを BF16 のまま GGUF を作る（llama.cpp の変換の道具・outtype bf16。約 +2GB）。v06 の重みの形に近づく
2. 1 に加えて、KV キャッシュを bf16 にする
3. 比べるときは、同じ入力で 1 組だけ（6 回目の入力）。費用の目安は 81 順3 と同じ（約 $0.6）

## 4. 変えたこと

| 項目 | 中身 |
|---|---|
| 選ぶ段のプロンプト | `docs/prompts/explanation_select_v0.2.txt` sha256 **b767abf2c302810b807289562a0f2050399b5af8490a13ab3f69f9bc0d223e70**（v0.1 19e818fb… は残す）。設定と版の記録も v0.2 |
| 禁止の語 | forbid-v0.5（＋同種・ラベルの英語の名前 11）。選ぶ段の軽い確かめとカードの確かめの両方 |
| 文の終わり | 流れのカードの ① の各文が「可能性があります。」で終わらなければ、その目的の ① を出さない。数は記録の `form_fixes.meaning_end_ng` |
| 番号を消す | `card.strip_ids`（`fix_form` の前）。数は記録の `form_fixes.ids_removed`（向きのレコードの meta の record に、版の記録と並べて残る） |
| スイッチ | `EXPLAIN_BOUND_FOR_NO_PURPOSE`（設定 `[pipeline] bound_for_no_purpose = false`）。版の鍵・版の記録に入る |
| 申し出の画面 | `/api/explanations/card` が `meaning_purposes` を返し、「どの目的の申し出ですか」で最初に選ぶ |

- 版の鍵（p81:…）は、select の sha が変わったので変わる。いま保存してある KAORU×PA のカードは、作り直すまで出ない（その場で作らない）

## 5. PR とテスト

- PR #225
- テスト 577〜581 を足した（番号を消す・文の終わり・禁止の語・スイッチ・申し出の目的）。全体 1195 通過・1 skip

## 6. ファイルの場所

- 指示書：`docs/instructions/81_addendum1.md`
- プロンプト：`docs/prompts/explanation_select_v0.2.txt`
- 実装：`src/explanation.py`・`scripts/explain_pipeline/{card,prompts,runner,runtime}.py`・`config/explain_runtime.toml`・`src/explain_service.py`・`app.py`・`templates/profile.html`
- テスト：`tests/test_t81a1_gaps.py`
- §2 の手元の比べ（コミットしない）：`local/t81/diag/`（render_compare.json は数だけ）
