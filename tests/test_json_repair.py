"""AI の出力をコピーしたときの壊れた JSON を受け付ける（2026-10 の実例。登録できなかった）。

壊れ方: 返答が Markdown として表示され、`[` `]` が `\\[` `\\]` になったり消えたりする。厳密に読めなかったときだけ、
配列のはずのキー（purposes・必要像・与え像・関心・生テキスト）の範囲で直す。直しても読めなければ位置を 1 行で返す。
実例と同じ壊れ方を、合成の文面で再現して確かめる（本人の語りはリポジトリに置かない）。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import pytest  # noqa: E402

import v5  # noqa: E402
from profile_view import parse_registration_text, repair_ai_json  # noqa: E402

RAW = ["一つ目の語り。", "二つ目の語り。", "三つ目の語り。", "四つ目の語り。"]


def _damaged():
    """実例と同じ壊れ方: 全角の引用符・purposes と最初の必要像は `\\[` `\\]`・2 つ目以降の配列は `[` `]` が無い。
    根拠は複数の引用を改行でつなぐ。"""
    def obj(x):
        return json.dumps(x, ensure_ascii=False)
    need = lambda t, r: obj({"文": t, "必須": r, "型": "関わり方"})
    p1 = ('{"purpose_id":"p1","向かう先":"向かう先1。","手段":"手段1。","必要像":\\[' + need("n1", True) + "," +
          need("n2", False) + '\\],"数値":{"gate_s":0.3,"gate_u":0.3},"根拠":"一つ目の語り。\\n三つ目の語り。"}')
    p2 = ('{"purpose_id":"p2","向かう先":"向かう先2。","手段":"手段2。","必要像":' + need("n3", True) + "," +
          need("n4", False) + ',"数値":{"gate_s":0.3,"gate_u":0.6},"根拠":"二つ目の語り。\\n五つ目の語り。"}')
    offers = obj({"文": "o1", "型": "関わり方"}) + "," + obj({"文": "o2", "型": "資源"})
    raw = ",".join(obj(t) for t in RAW)
    body = ('{"id":"alice","schema_version":"v5","generator":"GPT-6","_meta":{"source":"v5r3-A"},'
            '"purposes":\\[' + p1 + "," + p2 + '\\],"与え像":' + offers + ',"関心":' + obj({"文": "k1"}) + "," +
            obj({"文": "k2"}) + ',"現状":{"持っているもの":"h","できること_型":"c","縛られているもの":"b","未分類":""},'
            '"supporting_material":{"一行紹介":"x","要約文":"y","生テキスト":' + raw + "}}")
    return body.replace('"', "\u201c")                                   # iOS の全角引用符


def test_markdown_damaged_output_is_repaired():
    d = parse_registration_text(_damaged())
    assert [len(p["必要像"]) for p in d["purposes"]] == [2, 2]
    assert len(d["与え像"]) == 2 and len(d["関心"]) == 2 and d["supporting_material"]["生テキスト"] == RAW
    assert d["purposes"][1]["数値"] == {"gate_s": 0.3, "gate_u": 0.6}       # 配列の後のキーは目的の中に残る
    assert d["現状"]["持っているもの"] == "h"


def test_valid_json_is_not_touched():
    ok = {"a": ["x", "y"], "必要像": [{"文": "a/b \\\\[c]"}], "s": "\\\\[ok\\\\]"}
    assert parse_registration_text(json.dumps(ok, ensure_ascii=False)) == ok


@pytest.mark.parametrize("broken,expected", [
    ('{"生テキスト":"a","b","c"}', {"生テキスト": ["a", "b", "c"]}),
    ('{"与え像":{"文":"x"},{"文":"y"},"関心":{"文":"z"}}', {"与え像": [{"文": "x"}, {"文": "y"}], "関心": [{"文": "z"}]}),
    ('{"purposes":\\[{"必要像":{"文":"a"},"数値":{"gate_s":0}}\\]}', {"purposes": [{"必要像": [{"文": "a"}], "数値": {"gate_s": 0}}]}),
])
def test_repair_cases(broken, expected):
    assert parse_registration_text(broken) == expected


def test_unrepairable_reports_position_in_one_line():
    with pytest.raises(ValueError) as e:
        parse_registration_text('{"id": "x", "purposes": [ {"a": 1,, } ]}')
    msg = str(e.value)
    assert "JSONとして読めませんでした" in msg and "文字目付近" in msg and "\n" not in msg


def test_evidence_joined_by_newlines_is_checked_per_part():
    d = parse_registration_text(_damaged())
    ok, why = v5.validate(json.loads(json.dumps(d)))
    # p1 は改行でつないだ各引用が生テキストにある。p2 の 2 つ目は生テキストに無い（AI が入れ忘れた実例と同じ）ので、
    # どの引用かを示して止める。
    assert not ok and why == "目的 2: 根拠「五つ目の語り。」が生テキストに見つかりません（引用は原文のまま）"
    d["supporting_material"]["生テキスト"].append("五つ目の語り。")
    assert v5.validate(d)[0] is True
