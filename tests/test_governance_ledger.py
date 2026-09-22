"""指示書41 段階2（§4/§5）— ガバナンス台帳の新設・拡張イベントと、台帳だけからの再計算。

§11-5 payload・§11-6 台帳だけからの合意再計算・§11-7 配分に必要な情報の導出・
§11-8 intent.completed 下流が新経路の達成も読む、を検証する。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import ledger_events as le
import governance as gov
from member_ledger import publish_member_joined, members_hash
from agreement import evaluate_agreement


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def _genesis(db, ctx="c1", founder="u_alice"):
    """コミュニティのジェネシス（創設者の member.joined）だけを置く。"""
    publish_member_joined(ctx, founder, introduced_by=None, approved_by=[founder],
                          members_before=[], members_after=[founder], db_path=db)
    return ctx, founder


def _events(db, type_):
    return le.get_events(type_=type_, db_path=db)


# ── §11-5: 新設イベントの payload ────────────────────────────────────────────
def test_purpose_agreed_payload_shape():
    db = _db()
    ctx, founder = _genesis(db)
    rv = gov.resolve_ruleset_version(ctx, db_path=db)
    assert rv is not None                       # 初期値＝ジェネシスの event_hash（subject.created 不在時のフォールバック）
    ev = gov.publish_purpose_agreed(
        "talk_1", ctx, gov.conclusion_hash("目的の結論"),
        approvals=[founder], basis_seq=1, ruleset_version=rv,
        anchor_range_=None, discussion_hash_=gov.discussion_hash("経緯の全文"),
        declaration_hash=None, target_intent_id=None, db_path=db)
    p = _events(db, "purpose.agreed")[0]["payload"]
    for k in ("talk_id", "ctx", "conclusion_hash", "approvals", "basis_seq",
              "ruleset_version", "anchor_range", "discussion_hash",
              "declaration_hash", "target_intent_id", "changes_ruleset"):
        assert k in p, f"purpose.agreed に {k} が無い"
    assert p["approvals"] == [founder] and p["basis_seq"] == 1
    assert p["ruleset_version"] == rv
    # 本文は載せない（ハッシュのみ）
    assert p["conclusion_hash"] == gov.conclusion_hash("目的の結論")
    assert "conclusion" not in p and "body" not in p


def test_intent_launched_and_participant_and_completed_payloads():
    db = _db()
    ctx, founder = _genesis(db)
    rv = gov.resolve_ruleset_version(ctx, db_path=db)
    pa = gov.publish_purpose_agreed("talk_1", ctx, gov.conclusion_hash("やる"),
                                    approvals=[founder], basis_seq=1, ruleset_version=rv,
                                    anchor_range_=None, discussion_hash_=gov.discussion_hash("x"),
                                    db_path=db)
    launched = gov.publish_intent_launched("int_1", ctx, founder, pa["event_hash"], db_path=db)
    lp = _events(db, "intent.launched")[0]["payload"]
    assert lp["intent_id"] == "int_1" and lp["launcher"] == founder
    assert lp["purpose_ref"] == pa["event_hash"]          # どの合意から生まれたか（§7）

    # 参加（個人・1対1即時成立を想定して当事者2人が賛成）
    gov.publish_participant_joined("int_1", "u_bob", participant_kind="individual",
                                   approvals=[founder, "u_bob"], basis_seq=launched["seq"],
                                   ruleset_version=rv, anchor_range_=None,
                                   discussion_hash_=gov.discussion_hash("参加の話"),
                                   introduced_by=founder, db_path=db)
    jp = _events(db, "intent.participant.joined")[0]["payload"]
    assert jp["participant"] == "u_bob" and jp["participant_kind"] == "individual"
    assert jp["consent_ref"] is None and jp["introduced_by"] == founder
    assert set(jp["approvals"]) == {founder, "u_bob"}

    # 達成
    comp = gov.publish_intent_completed("int_1", gov.result_hash("できた"),
                                        approvals=[founder, "u_bob"], basis_seq=launched["seq"],
                                        ruleset_version=rv, anchor_range_=None,
                                        discussion_hash_=gov.discussion_hash("達成の話"), db_path=db)
    cp = _events(db, "intent.completed")[0]["payload"]
    assert cp["intent_id"] == "int_1"
    assert cp["result_hash"] == gov.result_hash("できた")     # §4-6: 下流が読むフィールドは不変
    assert set(cp["approvals"]) == {founder, "u_bob"}


def test_participant_kind_validated():
    db = _db()
    _genesis(db)
    try:
        gov.publish_participant_joined("int_x", "u", participant_kind="bogus",
                                       approvals=["u"], basis_seq=1, ruleset_version="rv",
                                       anchor_range_=None, discussion_hash_="h", db_path=db)
        assert False, "不正な participant_kind が通ってしまった"
    except ValueError:
        pass


# ── §11-6: 台帳だけから合意の成立を再計算できる ─────────────────────────────
def test_agreement_recomputable_from_ledger_only():
    db = _db()
    ctx, founder = _genesis(db)
    # 加入で b, c を足す（頭数の分母を basis_seq 時点から導出できるようにする）
    publish_member_joined(ctx, "u_bob", members_before=[founder], members_after=[founder, "u_bob"],
                          approvals=[founder, "u_bob"], basis_seq=1, ruleset_version="rv0",
                          anchor_range=None, discussion_hash="h", db_path=db)
    publish_member_joined(ctx, "u_carol", members_before=[founder, "u_bob"],
                          members_after=[founder, "u_bob", "u_carol"],
                          approvals=[founder, "u_bob"], basis_seq=2, ruleset_version="rv0",
                          anchor_range=None, discussion_hash="h", db_path=db)
    # 目的の合意を basis_seq=3（carol 加入後）で刻む。approvals は founder,bob（過半数）
    basis = le.get_last_event(db_path=db)["seq"]     # =3（分母 {founder,bob,carol}）
    # アンカーを2回跨いだ状態を作る
    le.append_event("system", "anchor.published", {"anchor_seq": 1, "scope": {}}, db_path=db)
    le.append_event("system", "anchor.published", {"anchor_seq": 2, "scope": {}}, db_path=db)
    gov.publish_purpose_agreed("talk_1", ctx, gov.conclusion_hash("結論"),
                               approvals=[founder, "u_bob"], basis_seq=basis, ruleset_version="rv0",
                               anchor_range_=gov.anchor_range(basis, db_path=db),
                               discussion_hash_=gov.discussion_hash("経緯"), db_path=db)
    # ── 再計算：刻まれた approvals と basis_seq だけを使い、分母は台帳から導出 ──
    ev = _events(db, "purpose.agreed")[0]["payload"]
    denom = gov.members_at(ev["ctx"], ev["basis_seq"], db_path=db)
    assert denom == {founder, "u_bob", "u_carol"}                 # 基準点時点の頭数
    crossed = gov.anchors_crossed(ev["basis_seq"], db_path=db)
    recomputed = evaluate_agreement(denominator=denom, approvals=ev["approvals"],
                                    dissents=set(), anchors_crossed=crossed)
    assert recomputed["agreed"] is True and recomputed["reason"] == "majority"
    # anchor_range も台帳から一致
    assert ev["anchor_range"]["count"] == 2


def test_later_members_do_not_change_recomputation():
    db = _db()
    ctx, founder = _genesis(db)
    publish_member_joined(ctx, "u_bob", members_before=[founder], members_after=[founder, "u_bob"],
                          db_path=db)
    basis = le.get_last_event(db_path=db)["seq"]        # 分母 {founder,bob}
    # 基準点の後に carol が加入
    publish_member_joined(ctx, "u_carol", members_before=[founder, "u_bob"],
                          members_after=[founder, "u_bob", "u_carol"], db_path=db)
    denom = gov.members_at(ctx, basis, db_path=db)
    assert denom == {founder, "u_bob"}                  # 後から増えた carol は分母に入らない（§5-5）


# ── §11-7: 配分に必要な情報が台帳から導出できる ─────────────────────────────
def test_distribution_inputs_derivable_from_ledger():
    db = _db()
    ctx, founder = _genesis(db)
    rv = gov.resolve_ruleset_version(ctx, db_path=db)
    pa = gov.publish_purpose_agreed("talk_1", ctx, gov.conclusion_hash("やる"),
                                    approvals=[founder], basis_seq=1, ruleset_version=rv,
                                    anchor_range_=None, discussion_hash_="h", db_path=db)
    launched = gov.publish_intent_launched("int_1", ctx, founder, pa["event_hash"], db_path=db)
    gov.publish_participant_joined("int_1", "u_ext", participant_kind="individual",
                                   approvals=[founder, "u_ext"], basis_seq=launched["seq"],
                                   ruleset_version=rv, anchor_range_=None, discussion_hash_="h",
                                   db_path=db)
    gov.publish_participant_joined("int_1", "c_other", participant_kind="community",
                                   approvals=[founder, "c_other"], basis_seq=launched["seq"],
                                   ruleset_version=rv, anchor_range_=None, discussion_hash_="h",
                                   consent_ref=pa["event_hash"], db_path=db)
    gov.publish_intent_completed("int_1", gov.result_hash("成果"),
                                 approvals=[founder, "u_ext", "c_other"], basis_seq=launched["seq"],
                                 ruleset_version=rv, anchor_range_=None, discussion_hash_="h", db_path=db)

    # (1) どの目的の合意から、どのプロジェクトが生まれたか
    lp = _events(db, "intent.launched")[0]["payload"]
    assert lp["purpose_ref"] == pa["event_hash"]
    # (2) プロジェクトの参加者と、個人かコミュニティか
    kinds = {p["payload"]["participant"]: p["payload"]["participant_kind"]
             for p in _events(db, "intent.participant.joined")}
    assert kinds == {"u_ext": "individual", "c_other": "community"}
    # コミュニティ参加には、その参加を決めた合意への consent_ref がある（§6）
    cref = [p["payload"]["consent_ref"] for p in _events(db, "intent.participant.joined")
            if p["payload"]["participant_kind"] == "community"][0]
    assert cref == pa["event_hash"]
    # (3) 立ち上げた側のコミュニティと、その時点のメンバー
    assert lp["ctx"] == ctx
    assert gov.members_at(ctx, launched["seq"], db_path=db) == {founder}
    # (4) 達成に合意した当事者
    cp = _events(db, "intent.completed")[0]["payload"]
    assert set(cp["approvals"]) == {founder, "u_ext", "c_other"}


# ── §11-8: intent.completed 下流が新経路の達成も正しく読む ───────────────────
def test_completed_downstream_reads_new_flow():
    db = _db()
    ctx, founder = _genesis(db)
    rv = gov.resolve_ruleset_version(ctx, db_path=db)
    pa = gov.publish_purpose_agreed("talk_1", ctx, gov.conclusion_hash("やる"),
                                    approvals=[founder], basis_seq=1, ruleset_version=rv,
                                    anchor_range_=None, discussion_hash_="h", db_path=db)
    gov.publish_intent_launched("int_new", ctx, founder, pa["event_hash"], db_path=db)
    gov.publish_intent_completed("int_new", gov.result_hash("成果本文"),
                                 approvals=[founder], basis_seq=2, ruleset_version=rv,
                                 anchor_range_=None, discussion_hash_="h", db_path=db)

    # 下流の契約（§4-6）: intent_id と result_hash だけを読む汎用リーダは、
    # 追加された共通項目を無視して正しく読める。
    reader = [(e["payload"]["intent_id"], e["payload"]["result_hash"])
              for e in _events(db, "intent.completed")]
    assert ("int_new", gov.result_hash("成果本文")) in reader

    # 既存の completed_episodes_for_prompt も intent.launched 経由で ctx を解決し、
    # 新経路の達成を件数に拾う（§11-8）。
    from intent_ledger import completed_episodes_for_prompt
    ep = completed_episodes_for_prompt(ctx, db_path=db)
    assert ep["count"] == 1
    assert ep["episodes"][0]["intent_id"] == "int_new"
