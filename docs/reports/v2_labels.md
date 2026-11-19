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

---

# 報告 v2 の正解づくり（続き）— 数は合った・食い違いは 1 組（止まる）

**この節にも、v2 の文（need・offer・reason）は書かない。id と数だけ**

## 結論

- 正解つきの一覧を `data/explanation_bench/v2/v2_items.json` に置いた（指示の v2_full.json。sha256 2c6c4a50…）
- **`v2_items.json` と `v2_draft.json` は同じ組**: id・need・offer・purpose が、60 組とも一字も違わない。GPT は同じ 60 組に正解を付けている
- **数は、作らせた文の内訳とすべて合った**（§1）
- **GPT との食い違いは 1 組: V54**。中身は `data/explanation_bench/v2/disagreements.md`（KAORU 用）にだけ書いた
- `v2_labels.json` はまだ作っていない（KAORU の決定を待つ）

## 1. 数の確かめ（v2_items.json）

| 項目 | 作らせた文の内訳 | v2_items.json |
|---|---|---|
| 組の数 | 60 | 60 |
| R1 経験 | 6 | 6 |
| R1 継続 | 2 | 2 |
| R1 現職 | 2 | 2 |
| R2 申告 | 5 | 5 |
| R3 所有 | 3 | 3 |
| R3 使える立場 | 2 | 2 |
| R4 関心 | 4 | 4 |
| 説明できる（計） | 24 | 24 |
| 説明できない | 36 | 36 |
| 判断できない | 0 | 0 |
| 領域の中 | 30 | 30 |
| 領域の外 | 30 | 30 |

- 説明できる組は、領域の中に 12・外に 12（作らせた文の「同じくらい」）
- 説明できない組は、領域の中に 18・外に 18
- 欄は 60 組とも id・need・offer・purpose・area・label・sub・basis・reason・aim の 10 個

## 2. 食い違った組（label と sub を比べた）

| id | v2_items | GPT |
|---|---|---|
| V54 | 説明できない | R4・関心 |

- 残りの 59 組は、label と sub が一致した
- 順1 の §3 の「GPT は R4 が 1 多く、説明できないが 1 少ない」は、この 1 組の違い

## 3. 次

KAORU が V54 を決めたら、その結果で `v2_labels.json` を作り、sha256 を README に書いて固定する。固定の後は正解を変えない。照合と LLM は、まだ動かさない。

---

# 報告 v2 の正解づくり（固定）— 正解を固定した（止まる）

**この節にも、v2 の文（need・offer・reason）は書かない。id と数だけ**

## 結論

- **正解を `data/explanation_bench/v2/v2_labels.json` に固定した**（2026-11-19・sha256 2c47105e6425e4424d49d780dc59ee483b87274d515bf6e4883b98f2f28826cf）。固定の後は正解を変えない
- **V54**: KAORU の決定（応える）で、R4・関心にした。食い違いの 2 案のうち、応える側（GPT の正解）にあたる
- **ほかの 59 組**: v2_items.json のまま（GPT とも一致していた）
- **記録**: v2_labels.json には、組ごとに v2_items と GPT の正解（label・sub）と決め方を残した。V54 は、KAORU が決めたことと、両方の正解と理由を残した
- **出どころの sha256**: v2_items.json・v2_gpt.json・v2_draft.json の sha256 も記録した
- **固定のテスト**: テスト 545（`tests/test_v2_labels_fixed.py`）。v2_labels.json の sha256 が変われば落ちる

## 固定した正解の数

| 正解 | 数 |
|---|---|
| R1 経験 | 6 |
| R1 継続 | 2 |
| R1 現職 | 2 |
| R2 申告 | 5 |
| R3 所有 | 3 |
| R3 使える立場 | 2 |
| R4 関心 | 5 |
| 説明できる（計） | 25 |
| 説明できない | 35 |
| 全部 | 60 |

- 作らせた文の内訳（R4 4・説明できない 36）と比べると、V54 の決定で R4 が 1 多く、説明できないが 1 少ない
- 照合と LLM は、まだ動かしていない
