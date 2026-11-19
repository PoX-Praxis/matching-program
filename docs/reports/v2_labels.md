# 報告 v2 の正解づくり — 材料を置いた・draft に正解が無い（止まる）

**指示:** `docs/instructions/v2_labels.md`　**材料:** `data/explanation_bench/v2/`
**この報告には、v2 の文（need・offer・reason）を書かない。id と数だけ**

## 結論

- 4 つのファイルを `data/explanation_bench/v2/` に置いた（渡されたまま。sha256 は README）
- **`v2_draft.json` に正解が入っていない**
  - 60 組とも、欄は id・need・offer・purpose の 4 つだけ
  - label・sub・area・basis・reason・aim は無い
- そのため、§2（draft の数の確かめ）と §3（2 つの正解の食い違い）はできない
- `disagreements.md` と `v2_labels.json` は作っていない
- 照合と LLM は動かしていない

## 1. 確かめられた数

| 項目 | v2_draft.json | v2_gpt.json |
|---|---|---|
| 組の数 | 60 | 60 |
| id | V01〜V60（抜け・重なりなし） | V01〜V60（抜け・重なりなし） |
| 欄 | id・need・offer・purpose | id・label・sub・basis・reason |
| purpose | 補完 53・共鳴 7 | — |
| need の 40 字超え・offer の 60 字超え | 0・0 | — |

## 2. GPT の正解の内訳（参考）

draft の正解が無いので、比べたのではない。作らせた文の内訳と並べただけ。

| 正解 | 作らせた文の内訳 | GPT の正解 |
|---|---|---|
| R1 経験 | 6 | 6 |
| R1 継続 | 2 | 2 |
| R1 現職 | 2 | 2 |
| R2 申告 | 5 | 5 |
| R3 所有 | 3 | 3 |
| R3 使える立場 | 2 | 2 |
| R4 関心 | 4 | 5 |
| 説明できない | 36 | 35 |
| 判断できない | 0 | 0 |

- GPT の正解は、作らせた内訳より R4 が 1 多く、説明できないが 1 少ない
- どの組かは、draft の正解が無いので分からない
- 領域の中と外の数は、どちらのファイルにも欄が無いので数えられない

## 3. 次に要るもの

**正解の入った draft**（作らせた文の出力の形で、label・sub・area・basis・reason・aim のある 60 組）。届いたら §2・§3 を行い、食い違いの中身を `disagreements.md`（KAORU 用）に書いて、報告して止まる。
