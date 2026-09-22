"""指示書41 §5 / §11-4 — 合意判定（純粋関数）のテスト。

決定性の要（§10 禁則）: 現在時刻・現在メンバー・DB に依存せず、入力だけで結論が決まる。
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agreement import evaluate_agreement as ev, DEFAULT_PERIOD_ANCHORS


# §11-4-1: 沈黙者がいても、定足数（過半数）と投票者の全員一致（反対なし）で成立する
def test_silent_members_do_not_block_majority():
    r = ev(denominator={"a", "b", "c"}, approvals={"a", "b"}, dissents=set(), anchors_crossed=2)
    assert r["agreed"] is True and r["reason"] == "majority"
    assert r["approvers"] == ["a", "b"]           # 台帳に刻むのは実際に賛成した2人だけ
    # 沈黙者 c は棄権（賛成として数えない）
    assert "c" not in r["approvers"]


# §11-4-2: 反対が1人でもあれば成立しない
def test_single_dissent_blocks():
    r = ev(denominator={"a", "b", "c"}, approvals={"a", "b"}, dissents={"c"}, anchors_crossed=9)
    assert r["agreed"] is False and r["reason"] == "vetoed"


# §11-4-3: 賛成が分母の過半数に届かなければ成立しない
def test_below_majority_fails():
    r = ev(denominator={"a", "b", "c"}, approvals={"a"}, dissents=set(), anchors_crossed=9)
    assert r["agreed"] is False and r["reason"] == "no_quorum"
    # ちょうど半分（分母4で2）も過半数ではない
    r2 = ev(denominator={"a", "b", "c", "d"}, approvals={"a", "b"}, dissents=set(), anchors_crossed=9)
    assert r2["agreed"] is False and r2["reason"] == "no_quorum"
    # 過半数（分母4で3）なら成立
    r3 = ev(denominator={"a", "b", "c", "d"}, approvals={"a", "b", "c"}, dissents=set(), anchors_crossed=9)
    assert r3["agreed"] is True and r3["reason"] == "majority"


# §11-4-4: 全員が明示的に賛成すれば、期間を待たずに成立する（§5-3）
def test_unanimous_is_immediate():
    r = ev(denominator={"a", "b", "c"}, approvals={"a", "b", "c"}, dissents=set(), anchors_crossed=0)
    assert r["agreed"] is True and r["immediate"] is True and r["reason"] == "immediate_unanimous"


# §5-3: 1対1（分母2）は双方の賛成で即時成立
def test_one_to_one_immediate():
    r = ev(denominator={"launcher", "joiner"}, approvals={"launcher", "joiner"},
           dissents=set(), anchors_crossed=0)
    assert r["agreed"] is True and r["immediate"] is True


# 期間未満・全員未賛成なら保留（pending）。過半数でも期間を待つ。
def test_pending_before_period():
    r = ev(denominator={"a", "b", "c"}, approvals={"a", "b"}, dissents=set(), anchors_crossed=1)
    assert r["agreed"] is False and r["reason"] == "pending_period"


# §11-4-5: 基準点の後にメンバーが増えても判定は変わらない
def test_later_joiners_do_not_change_outcome():
    # 基準点の分母は {a,b,c}。後から d,e が増えて賛成しても分母外なので数えない。
    base = ev(denominator={"a", "b", "c"}, approvals={"a", "b"}, dissents=set(), anchors_crossed=2)
    after = ev(denominator={"a", "b", "c"}, approvals={"a", "b", "d", "e"},
               dissents=set(), anchors_crossed=2)
    assert after["agreed"] == base["agreed"] == True
    assert after["approvers"] == base["approvers"] == ["a", "b"]   # d,e は無視
    # 分母外からの反対も判定に影響しない
    after2 = ev(denominator={"a", "b", "c"}, approvals={"a", "b"}, dissents={"d"}, anchors_crossed=2)
    assert after2["agreed"] is True and after2["reason"] == "majority"


# §11-4-6 / §5-4: 作成者1人のコミュニティは作成者の賛成で成立（特別分岐なし）
def test_bootstrap_single_member():
    r = ev(denominator={"founder"}, approvals={"founder"}, dissents=set(), anchors_crossed=0)
    assert r["agreed"] is True and r["immediate"] is True
    # 作成者が賛成していなければ成立しない
    r0 = ev(denominator={"founder"}, approvals=set(), dissents=set(), anchors_crossed=9)
    assert r0["agreed"] is False


# 分母が空なら判定不能・不成立
def test_empty_denominator():
    r = ev(denominator=set(), approvals={"x"}, dissents=set(), anchors_crossed=9)
    assert r["agreed"] is False and r["reason"] == "no_denominator"


# 決定性: 同じ入力は同じ結論（順序・集合型に依らない）
def test_determinism_and_input_shapes():
    a = ev(denominator=["a", "b", "c"], approvals=["b", "a"], dissents=[], anchors_crossed=2)
    b = ev(denominator=("c", "b", "a"), approvals=("a", "b"), dissents=set(), anchors_crossed=2)
    assert a == b


# 期間 N は可変（初期値は2）
def test_period_threshold_configurable():
    assert DEFAULT_PERIOD_ANCHORS == 2
    r = ev(denominator={"a", "b", "c"}, approvals={"a", "b"}, dissents=set(),
           anchors_crossed=1, period_anchors=1)
    assert r["agreed"] is True and r["reason"] == "majority"
