# v2 の正解づくり（G2・G3／物差し／枝）

**日付:** 2026-11-19（KAORU から）
**止まるところ:** 正解を固定したら止まる。照合も LLM もまだ動かさない

## 渡したもの

- v2_draft.json（Claude の別の会話が作った 60 組と正解）
- v2_gpt.json（GPT の一時チャットが、同じ 60 組に独立に付けた正解）
- PoX_v2を作る_別の会話に渡す文_2026-11-19.txt（v2 を作らせた文）
- PoX_v2の正解_もう一つの確かめ_GPTに渡す文_2026-11-19.txt（GPT に確かめさせた文）

## やること

1. 4 つを data/explanation_bench/v2/ に保存する（架空の文なのでリポジトリに入れてよい）。2 つの .txt は、v2 の作り方の記録として残す
2. v2_draft.json の数を確かめる（全部で 60・説明できる 24〔R1 経験 6・継続 2・現職 2・R2 5・R3 所有 3・使える立場 2・R4 4〕・説明できない 36・領域の中 30／外 30）。合わなければ数だけ報告する
3. 2 つの正解（label と sub）を比べて、食い違った組の id の一覧を作る
4. 報告して止まる

## 報告の決まり（大事）

- 報告に、v2 の文（need・offer・reason）を書かない。id と数だけにする（報告は表を作った Claude にも渡るため。Claude は v2 の中身を見てはいけない）
- 食い違った組の中身は、KAORU 用の別のファイル data/explanation_bench/v2/disagreements.md にだけ書く（id・need・offer・両方の正解と理由）

KAORU が食い違いを決めたら、その結果で v2_labels.json を作り、sha256 を記録して固定する。固定の後は、正解を変えない。

---

## 続き（2026-11-19・KAORU から）

v2 の正解づくりの続き。正解つきの JSON を v2_full.json として渡す（データ名は v2_items）。最初に渡した v2_draft.json は、正解の欄を消した一覧だった。

1. v2_full.json を data/explanation_bench/v2/ に保存し、sha256 を README に足す
2. v2_full.json の id・need・offer・purpose が、v2_draft.json と 60 組とも一字も違わないか確かめる（GPT が同じ組に正解を付けたかの確かめ）。違う組があれば、id と数だけ報告して止まる
3. 前の指示の §2（数の確かめ）と §3（食い違った組の id の一覧）を行う
4. 報告して止まる

報告の決まりは前と同じ：報告に v2 の文を書かない（id と数だけ）。食い違った組の中身は data/explanation_bench/v2/disagreements.md にだけ書く。

---

## 固定（2026-11-19・KAORU から）

v2 の正解を固定する。V54 は KAORU が「応える」に決めた（ほかの 59 組は v2_items.json のまま）。

1. この結果で data/explanation_bench/v2/v2_labels.json を作る（V54 を KAORU が決めたことと、v2_items と GPT の正解を、記録として残す）
2. sha256 を README に書いて固定する。固定の後は、正解を変えない
3. 報告して止まる（報告は id と数だけ。v2 の文は書かない）
