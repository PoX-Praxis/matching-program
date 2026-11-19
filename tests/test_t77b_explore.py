"""指示書77b 追補（探索）— 説明できる組のうち、照合の表 v0.3.1-dev が落とした 23 組。テスト 553。

553 探索のファイルは記録のとおり（23 組の id・段・必要・相手）。形だけ変えた確かめは、書いたとおりの結果になる
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

import match_table as MT  # noqa: E402
import t77b_measure as M  # noqa: E402

EXPLORE = os.path.join(ROOT, "data", "explanation_bench", "v2", "77b_drops_explore.md")
V = "v0.3.1-dev"

# 形だけ変えた確かめ（§3）: id → (必要, 相手, 通るか, 落ちる段)
VARIANTS = {
    "V03": ("高齢の方にスマホの使い方を教えることができる人。", "平日の午後なら、高齢の方にスマホの操作を教えることができます。", True, None),
    "V17": ("職人の技を次へつなぐことに関心がある人。", "手仕事の技が途絶えないようにすることに、関心があります。", True, None),
    "V01": ("若い作家の作品に助言できる人。", "二十代の書き手の原稿を読んで、感想と改善点を伝えることができます。", False, "述語"),
    "V10": ("体育館を貸し出せる人。", "小学校の体育館の管理を任されていて、夜なら利用の受付ができる。", False, "種類"),
    "V21": ("集まりに使える部屋を貸し出せる人。", "公民館の鍵を任されていて、和室の予約の受付ができる。", False, "種類"),
    "V12": ("小学生の宿題を見ることができる人。", "週に一度なら、小学生の宿題を見ることができます。", False, "述語"),
    "V13": ("地元の産品の販路を開いてきた人。", "地域の特産品を都内の店につなぐことを仕事にしている。", False, "述語"),
    "V59": ("診療報酬の請求をしてきた人。", "クリニックで、レセプトの作成と請求を仕事にしている。", False, "述語"),
    "V29": ("練習用の電子ピアノを貸し出せる人。", "使ってない電子ピアノが家にあるから、貸せる。", False, "述語"),
    "V36": ("米づくりを続けてきた人。", "もう二十年、山あいの田んぼで稲を育てている。", False, "述語"),
    "V52": ("一人暮らしの方の見守りを続けてきた人。", "十年、近所の一人暮らしの方の家を毎朝訪ねている。", False, "述語"),
    "V51": ("外国の言葉の学び方に関心がある人。", "大人になってからの外国語の学び方に関心があります。", False, "領域"),
}


@pytest.fixture(scope="module")
def doc():
    return open(EXPLORE, encoding="utf-8").read()


def test_t553_file_follows_the_record(doc):
    rec = json.load(open(M.RECORD, encoding="utf-8"))
    dropped = {x["id"]: x["段"] for x in rec["v2"]["数"]["線2"]["出なかった組"]}
    items = {it["id"]: it for it in M.load_v2()}
    blocks = re.split(r"\n### ", doc)[1:]
    assert len(blocks) == 23 and len(dropped) == 23
    for b in blocks:
        i = b[:3]
        assert i in dropped, i
        assert f"- 必要：{items[i]['need']}\n" in b and f"- 相手：{items[i]['offer']}\n" in b, i
        assert f"- 段：{dropped[i]}" in b, i
        gold = f"{items[i]['gold']}・{items[i]['gold_sub']}"
        assert f"正解 {gold}）" in b.splitlines()[0], i
    rows = [ln for ln in doc.splitlines() if re.match(r"\| V\d\d \|", ln)]
    table = {r.split("|")[1].strip(): r.split("|")[4].strip() for r in rows if r.split("|")[2].strip().startswith("R")}
    assert table == dropped
    assert "V60" not in table


@pytest.mark.parametrize("i", sorted(VARIANTS))
def test_t553_form_only_variants(i, doc):
    need, offer, passes, where = VARIANTS[i]
    r = MT.match(need, offer, version=V)
    assert r["passed"] is passes, (i, r.get("where"), r.get("why"))
    if not passes:
        assert r["where"] == where, (i, r.get("where"), r.get("why"))
    else:
        gold = next(it for it in M.load_v2() if it["id"] == i)
        assert (r["relation"], r["kind"]) == (gold["gold"], gold["gold_sub"])
    orig = next(it for it in M.load_v2() if it["id"] == i)
    assert not MT.match(orig["need"], orig["offer"], version=V)["passed"]        # 元の文は落ちたまま
