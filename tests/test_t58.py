"""指示書58: テスト4の不整合の解消（224〜226）。"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]
import app as appmod  # noqa: E402


def TPL(name):
    return open(os.path.join(ROOT, "templates", name), encoding="utf-8").read()


# ── 224 根拠の「応える側」は与え像（v4 は現状）。相手の必要像を提供物として出さない ─────────────
def test_t224_offer_side_is_never_a_necessity():
    for has_offer in (True, False):
        side = {"has_offer": has_offer}
        items = appmod._reason_items(
            [{"kind": "fill_mine", "mine": "N1", "theirs": "O1"},
             {"kind": "fill_theirs", "mine": "O2", "theirs": "N2"}], side, side)
        a, b = items
        assert (a["need_label"], a["need"], a["offer"]) == ("あなたの必要像", "N1", "O1")
        assert a["offer_label"] == ("相手の与え像" if has_offer else "相手の現状")
        assert (b["need_label"], b["need"], b["offer"]) == ("相手の必要像", "N2", "O2")
        assert b["offer_label"] == ("あなたの与え像" if has_offer else "あなたの現状")
        assert all("必要像" not in x["offer_label"] for x in items)


def test_t224_offer_half_is_its_own_block():
    """必要像の側と応える側を別の塊にする（1 つの塊だと長い必要像で「…が応えています」が見えなかった）。"""
    part = TPL("_match_reason.html")
    body = part[part.index("function line(x)"):part.index("function reasonHtml")]
    assert 'class="mr-need"' in body and 'class="mr-give"' in body
    assert "が応えています" in body


# ── 227 引用は語・句の途中で切らない（文の区切りまで。残りは「続きを見る」）───────────────────
def _run_cut(samples):
    import json as _j
    import shutil
    import subprocess
    import pytest
    if not shutil.which("node"):
        pytest.skip("node が無い環境")
    part = TPL("_match_reason.html")
    js = part[part.index("<script>") + len("<script>"):part.index("</script>")]
    prog = ("const window = {};\n" + js +
            f"\nconsole.log(JSON.stringify({_j.dumps(samples, ensure_ascii=False)}.map(window.PoXReason.cut)));")
    out = subprocess.run(["node", "-e", prog], capture_output=True, text=True, check=True).stdout
    return _j.loads(out)


def test_t227_quote_is_cut_at_sentence_boundary():
    a = "能力の提供だけでなく共に事業を育てる立場として関わってきた人。構造的アプローチに関心があり、議論を形にできる人。"
    v4 = "設計の経験がある / 構造的アプローチに関心があり、検証まで回せる / 時間が限られている"
    r = _run_cut([a, v4, "区切りの無い一文", "一文だけ。"])
    assert r[0] == {"head": "能力の提供だけでなく共に事業を育てる立場として関わってきた人。",
                    "rest": "構造的アプローチに関心があり、議論を形にできる人。"}
    assert r[1] == {"head": "設計の経験がある", "rest": "構造的アプローチに関心があり、検証まで回せる / 時間が限られている"}
    assert r[2] == {"head": "区切りの無い一文", "rest": ""}               # 区切りが無ければ丸ごと（語の途中で切らない）
    assert r[3] == {"head": "一文だけ。", "rest": ""}


def test_t227_no_line_clamp_on_quotes_and_fold_exists():
    part = TPL("_match_reason.html")
    body = part[part.index("function cut(t)"):part.index("function reasonHtml")]
    assert "clamp-2" not in body                                        # 行数の丸めを使わない
    assert "<details" in body and "続きを見る" in body                  # 全文は折りたたみで


# ── 225 受信箱にもマイページと同じ承認待ちが出る ───────────────────────────────────────
def test_t225_inbox_loads_reason_component_outside_title():
    src = TPL("inbox.html")
    title = re.search(r"{% block title %}(.*?){% endblock %}", src, re.S).group(1)
    assert "include" not in title                                   # <title> の中では script が動かない
    head = re.search(r"{% block head %}(.*?){% endblock %}", src, re.S).group(1)
    assert '{% include "_match_reason.html" %}' in head
    html = appmod.app.test_client().get("/inbox").get_data(as_text=True)
    t = html[html.index("<title>"):html.index("</title>")]
    assert "<script" not in t and "window.PoXReason" in html


def test_t225_inbox_uses_session_identity_like_mypage():
    src = TPL("inbox.html")
    assert "await PoX.me()" in src and "myId = sid" in src


# ── 226 照合 0 件でも、承認待ちの申し出があれば受信箱へ ─────────────────────────────────
def test_t226_zero_results_links_to_inbox_when_offers_pending():
    src = TPL("connect.html")
    zero = src[src.index("if (!results.length)"):src.index('status.innerHTML = "";')]
    assert "PoXReason.fetchOffers()" in zero and "/inbox?id=" in zero
    assert "未承認の接続の申し出が届いています" in zero


def test_t58_connect_header_wording():
    src = TPL("connect.html")
    assert "あなたの目的ごとの必要像から、いま噛み合いそうな相手を照合します。" in src
    assert "必要像と意志から" not in src
