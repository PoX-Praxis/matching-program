r"""指示書64 §2（PR-64B）— 指示書62-b の未実装分（テスト 315〜322）。

- gate の範囲外・数値でない値は、丸めずに登録を拒否する（62-b §1-1）
- `\\_` `\\*` `\\[`（正しい JSON のバックスラッシュ文字）は補正しない（§1-2）
- 確認画面を開いた時点で、ハンドルの重なりを知らせる（§1-4）
- 全角の引用符は、厳密に読めなかったときだけ、JSON の区切りの位置にあるものだけを直す（§2-1）
- プロンプト A の 139 行目の注記（§3）
- `\(` `\)` を文字列の外だけ `[` `]` に戻す（§7）
62-b のテスト番号（277〜）は既に別の項目に使われているので、続きの 315 から振る（指示書64 §2）。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import drafts  # noqa: E402
import handles  # noqa: E402
import v5  # noqa: E402
from profile_view import parse_registration_text, parse_registration_text_report  # noqa: E402
from test_t62_registration import _cli, _doc, _one_line, _post, db  # noqa: E402,F401

FIX = "AI に『数値を範囲内で出し直して』と頼んでください"


def _with_gate(**nums):
    d = _doc()
    d["purposes"][0]["数値"] = nums
    return d


# ── 315 gate が 0〜1 の範囲外 → 登録不可（丸めない）─────────────────────────────────────
@pytest.mark.parametrize("key,val", [("gate_s", 1.2), ("gate_s", -0.1), ("gate_u", 1.5), ("gate_u", -1)])
def test_t315_gate_out_of_range_rejected(db, key, val):
    nums = {"gate_s": 0.3, "gate_u": 0.3, key: val}
    ok, why = v5.validate(_with_gate(**nums))
    assert not ok and why == f"目的 1: {key} の値 {val} が 0〜1 の範囲にありません。{FIX}"
    r = _post(_one_line(_with_gate(**nums)))
    assert r.status_code == 400 and r.get_json()["error"] == why               # 下書きの時点で止まる（登録不可）


# ── 316 gate が数値でない（文字列・null・真偽値・欠け）→ 登録不可 ───────────────────────────
@pytest.mark.parametrize("nums,why", [
    ({"gate_s": "0.3", "gate_u": 0.3}, f'目的 1: gate_s の値 "0.3" が数値ではありません。{FIX}'),
    ({"gate_s": 0.3, "gate_u": None}, f"目的 1: gate_u の値 null が数値ではありません。{FIX}"),
    ({"gate_s": True, "gate_u": 0.3}, f"目的 1: gate_s の値 true が数値ではありません。{FIX}"),
    ({"gate_s": 0.3}, f"目的 1: gate_u の値がありません。{FIX}"),
])
def test_t316_gate_not_number_rejected(db, nums, why):
    assert v5.validate(_with_gate(**nums)) == (False, why)


def test_t316b_numbers_not_clamped_at_confirm():
    """確定の側も丸めない（検証をすり抜けた値は止める）。以前の _clamp_numbers は範囲に合わせていた。"""
    with pytest.raises(ValueError):
        appmod._v5_numbers({"gate_s": 1.2, "gate_u": 0.3})
    assert appmod._v5_numbers({"gate_s": 1, "gate_u": 0}) == {"gate_s": 1.0, "gate_u": 0.0}
    assert not hasattr(appmod, "_clamp_numbers")


# ── 317 境界値 0・1 は通る ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("nums", [{"gate_s": 0, "gate_u": 1}, {"gate_s": 1.0, "gate_u": 0.0}])
def test_t317_boundaries_pass(db, nums):
    assert v5.validate(_with_gate(**nums))[0] is True
    assert _post(_one_line(_with_gate(**nums))).status_code == 201


# ── 318 `\\_` `\\*` `\\[`（正しい JSON のバックスラッシュ文字）は補正しない ──────────────────────
def test_t318_escaped_backslash_kept():
    # 構造の位置の \[ \] で厳密な解析が失敗し、補正が動く。そのときも文字列の中の \\_ \\* \\[ は触らない。
    broken = '{"purposes":\\[{"文":"a\\\\_b c\\\\*d e\\\\[f","k\\_x":"g\\_h"}\\]}'
    d, kinds = parse_registration_text_report(broken)
    assert d == {"purposes": [{"文": "a\\_b c\\*d e\\[f", "k_x": "g_h"}]}
    assert any("\\[" in k for k in kinds) and any("\\_" in k for k in kinds)


# ── 319 文字列の中の全角の引用符は保存後も変わらない ──────────────────────────────────────
def test_t319_curly_quotes_in_content_unchanged(db):
    doc = _doc()
    doc["supporting_material"]["生テキスト"][0] = "私は“PoX”という仕組みを作るのが好きです。"
    doc["purposes"][0]["根拠"] = "私は“PoX”という仕組みを作るのが好きです。"
    doc["関心"] = [{"文": "‘構造’に関心がある。"}]
    text = _one_line(doc)
    d, kinds = parse_registration_text_report(text)                          # 正しい JSON → 補正なし
    assert d == doc and kinds == []
    r = _post(text)
    assert r.status_code == 201 and "repairs" not in r.get_json()
    saved = drafts.get_draft(r.get_json()["draft_id"], db_path=db)["payload"]
    assert saved["supporting_material"]["生テキスト"][0] == "私は“PoX”という仕組みを作るのが好きです。"
    assert saved["関心"][0]["文"] == "‘構造’に関心がある。"


def test_t319b_curly_delimiters_fixed_content_kept():
    """iOS の自動変換で区切りまで全角になった JSON: 区切りだけを戻し、中身の “PoX” は残す。補正として告知する。"""
    body = '{“id”: “x”, “s”: “私は“PoX”を作る”, “a”: [“1”, “2”]}'
    d, kinds = parse_registration_text_report(body)
    assert d == {"id": "x", "s": "私は“PoX”を作る", "a": ["1", "2"]}
    assert any("全角の引用符" in k for k in kinds)
    reg = open(os.path.join(ROOT, "templates", "register.html"), encoding="utf-8").read()
    assert ".replace(/[“”]/g" not in reg and ".replace(/[‘’]/g" not in reg      # 画面の側でも中身を置き換えない


# ── 320 確認画面でハンドルの重なりを知らせる（相手の情報は出さない）─────────────────────────────
def test_t320_handle_taken_on_preview(db):
    handles.set_handle("u_other", "kaoru_2026", db_path=db)
    r = _post(_one_line(_doc()), sid="u_new")
    d = r.get_json()
    assert r.status_code == 201 and d["needs_handle"] is True and d["handle_taken"] is True
    assert "u_other" not in json.dumps(d, ensure_ascii=False)
    reg = open(os.path.join(ROOT, "templates", "register.html"), encoding="utf-8").read()
    assert 'const HANDLE_TAKEN = "このハンドルは既に使われています"' in reg
    assert "draft.handle_taken ? HANDLE_TAKEN" in reg and "/api/handle/check?h=" in reg
    assert 'oninput="checkHandleSoon(this.value)"' in reg
    assert reg.count('id="handleTakenNote"') == 1 and reg.count('id="handleNote"') == 1   # 既存の案内と id を分ける
    c = _cli("u_new")                                                         # 確定時の 409 は残す（守りは確定時）
    assert c.post(f"/v4/drafts/{d['draft_id']}/confirm", json={"handle": "kaoru_2026"}).status_code == 409


# ── 321 プロンプト A の 139 行目の注記 ─────────────────────────────────────────────────
def test_t321_prompt_a_line_139():
    lines = open(os.path.join(ROOT, "docs", "prompts", "v5_A_dialogue.txt"), encoding="utf-8").read().split("\n")
    assert lines[138] == ("※ 下記は構造を示すために整形して表示しているだけである。**実際の第3段の出力は、この構造を改行なしの"
                          "1行に潰したミニファイ形式で、```json のコードブロック1つの中に**入れて行うこと（第3段の形式規則1〜7）。")
    assert '"source": "v5r4-A"' in "\n".join(lines)                           # 版は上げない


# ── 322 `\(` `\)` を文字列の外だけ [ ] に戻す（ChatGPT の数式記号）────────────────────────────
def _paren_damaged():
    """62-b §7 の実例と同じ形: purposes が `\\(` `\\)`、キーが `purpose\\_id`。purposes を最後に置き、末尾は `"\\)}`。"""
    d = _doc()
    purposes = d.pop("purposes")
    d["purposes"] = purposes
    s = _one_line(d)
    s = s.replace('"purposes":[', '"purposes":\\(', 1).replace('"purpose_id"', '"purpose\\_id"')
    assert s.endswith("]}")
    return s[:-2] + "\\)}"


def test_t322_math_parens_repaired(db):
    text = _paren_damaged()
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
    r = _post(text)
    d = r.get_json()
    assert r.status_code == 201, d
    assert "数式記号になった括弧（\\( \\)）を [ ] に戻した" in d["repairs"]
    assert any("\\_" in k for k in d["repairs"])
    assert d["array_counts"]["purposes"] == 1


def test_t322b_parens_inside_strings_untouched():
    kept = parse_registration_text('{"a":\\(1\\),"s":"式 \\\\(x\\\\) を書く"}')
    assert kept == {"a": [1], "s": "式 \\(x\\) を書く"}                     # 文字列の中の \\( は本人の文字
    from profile_view import repair_ai_json_report
    fixed, _ = repair_ai_json_report('{"a":\\(1\\),"s":"x \\(y\\)"}')
    assert fixed == '{"a":[1],"s":"x \\(y\\)"}'                                # 文字列の中の \( には触れない
