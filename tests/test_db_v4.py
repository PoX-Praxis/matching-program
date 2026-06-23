"""
Step 6 DoD 検証テスト — プラットフォーム統合 / v4 取り込み・照合（F章）

DoD（Postgres 無しで MemoryStore により全オーケストレーションを検証）:
  - ingest_profile_v4: raw 保存 / redacted 保存 / PII status / ② 必要像生成 / 4ベクトル保存
  - I章: raw を embedding/LLM に直接渡さない（防御 redact）
  - match_v4: ランキング生成 / γ,p,α,β が derived_necessity から流れる / ledger_v4 監査記録
  - 跨プール禁止: shortlist は同 model_tag のみ（別 model_tag を混ぜない）
  - 非破壊: v4 ストアは既存 seekers/profiles に触れない
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db_v4 import ingest_profile_v4, match_v4, MemoryStore, _union_shortlist
from embedding_config import MODEL_TAG, FULL_DIM, SHORT_DIM
from necessity_gen import compute_gamma
from match_config import GAMMA_MAX


def _profile(will, want=None, extra=None):
    sup = {"求めている": want or "未取得", "意志要求の素材": "未取得",
           "連言選言の素材": "未取得", "チャネル重み素材": "未取得"}
    p = {
        "will_text": will,
        "state_have": "現場の知識",
        "state_can_type": "動ける型",
        "state_bound": "技術がない",
        "state_unsorted": "",
        "supporting_raw": sup,
    }
    if extra:
        p.update(extra)
    return p


# ── ingest_profile_v4 ─────────────────────────────────────────────────────────
def test_ingest_saves_profile_raw_and_redacted():
    store = MemoryStore()
    p = _profile("農家と店をつなぎたい 連絡 a@b.com",
                 want="アプリを実装できるエンジニア")
    ingest_profile_v4(store, "u1", p)
    saved = store.profiles["u1"]
    # raw は原文保持（will_text の生値は profiles_v4 に保存される）
    assert saved["will_text"] == "農家と店をつなぎたい 連絡 a@b.com"
    # supporting_redacted が作られ status が立つ
    assert saved["pii_redaction_status"] == "structural_done"
    assert "supporting_redacted" in saved


def test_ingest_generates_necessity_owned_by_two():
    store = MemoryStore()
    ingest_profile_v4(store, "u1", _profile("意志", want="必要な相手"))
    nec = store.necessity[("u1", MODEL_TAG)]
    # ② が s,u,γ,p,α,β と src_input_hash を所有
    for k in ("necessity_text", "gate_s", "gate_u", "gamma", "p_sharpness",
              "alpha", "beta", "src_input_hash", "generator_prompt_version"):
        assert k in nec
    assert nec["gamma"] == compute_gamma(nec["gate_s"], nec["gate_u"], GAMMA_MAX)


def test_ingest_saves_8_vectors_correct_dims():
    store = MemoryStore()
    ingest_profile_v4(store, "u1", _profile("意志"))
    v = store.vectors[("u1", MODEL_TAG)]
    for k in ("will_symmetric", "will_passage", "state_passage", "necessity_query"):
        assert len(v[k]) == FULL_DIM
    for k in ("will_sym_256", "will_pas_256", "state_pas_256", "necessity_q_256"):
        assert len(v[k]) == SHORT_DIM


def test_ingest_defensive_redaction_into_embedding():
    """I章: raw に PII があっても embedding 入力は redact される。"""
    from embedding_service import embed
    store = MemoryStore()
    ingest_profile_v4(store, "u1", _profile("連絡は alice@example.com まで"))
    v = store.vectors[("u1", MODEL_TAG)]
    expected, _ = embed("連絡は [EMAIL] まで", "symmetric")
    assert v["will_symmetric"] == expected


def test_ingest_uses_injected_generator():
    store = MemoryStore()
    def fake(inputs):
        return {"necessity_text": "注入された必要像", "gate_s": 0.7, "gate_u": 0.3,
                "p_sharpness": -0.5, "alpha": 1.0, "beta": 2.0, "evidence_span": "x"}
    out = ingest_profile_v4(store, "u1", _profile("意志"), generator_fn=fake)
    assert out["necessity"]["necessity_text"] == "注入された必要像"
    assert store.necessity[("u1", MODEL_TAG)]["beta"] == 2.0


def test_ingest_returns_profile_id():
    store = MemoryStore()
    out = ingest_profile_v4(store, "uX", _profile("意志"))
    assert out["profile_id"] == "uX"


# ── match_v4 ──────────────────────────────────────────────────────────────────
def _seed_pool(store, n=3):
    for i in range(n):
        ingest_profile_v4(store, f"c{i}", _profile(f"候補{i}の意志",
                          want=f"候補{i}が必要とする相手"))


def test_match_returns_ranking():
    store = MemoryStore()
    ingest_profile_v4(store, "seeker", _profile("つなぎたい", want="実装者"))
    _seed_pool(store, 3)
    out = match_v4(store, "seeker")
    assert out["seeker_id"] == "seeker"
    assert out["model_tag"] == MODEL_TAG
    assert len(out["results"]) >= 1
    for r in out["results"]:
        assert {"candidate_id", "score", "attribution"} <= set(r)
    # 自分自身は候補に含まれない
    assert all(r["candidate_id"] != "seeker" for r in out["results"])


def test_match_scores_descending():
    store = MemoryStore()
    ingest_profile_v4(store, "seeker", _profile("つなぎたい", want="実装者"))
    _seed_pool(store, 4)
    scores = [r["score"] for r in match_v4(store, "seeker")["results"]]
    assert scores == sorted(scores, reverse=True)


def test_match_writes_ledger_audit():
    store = MemoryStore()
    ingest_profile_v4(store, "seeker", _profile("つなぎたい", want="実装者"))
    _seed_pool(store, 2)
    match_v4(store, "seeker")
    assert len(store.ledger) >= 1
    ev = store.ledger[0]
    assert ev["seeker_id"] == "seeker"
    assert ev["event"] == "match_ranked"
    # 監査メタデータ（律速軸・γ・チャネル類似）が記録される（F章）
    for k in ("score", "limiting_axis", "gamma", "alpha", "beta", "model_tag"):
        assert k in ev["payload"]


def test_match_no_ledger_when_disabled():
    store = MemoryStore()
    ingest_profile_v4(store, "seeker", _profile("つなぎたい"))
    _seed_pool(store, 2)
    match_v4(store, "seeker", write_ledger=False)
    assert store.ledger == []


def test_match_gamma_flows_from_necessity():
    """derived_necessity の γ が照合に反映される（γ=0 で c チャネル無視）。"""
    store = MemoryStore()
    def gamma_zero(inputs):
        return {"necessity_text": "必要", "gate_s": 0.0, "gate_u": 0.0,
                "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0, "evidence_span": ""}
    ingest_profile_v4(store, "seeker", _profile("意志"), generator_fn=gamma_zero)
    _seed_pool(store, 2)
    # γ=0 → ledger に gamma=0 が記録される
    match_v4(store, "seeker")
    assert store.ledger[0]["payload"]["gamma"] == 0.0


def test_match_unknown_seeker_raises():
    store = MemoryStore()
    _seed_pool(store, 2)
    try:
        match_v4(store, "missing")
        assert False
    except ValueError:
        pass


def test_match_top_k_limits():
    store = MemoryStore()
    ingest_profile_v4(store, "seeker", _profile("意志"))
    _seed_pool(store, 5)
    out = match_v4(store, "seeker", top_k=2)
    assert len(out["results"]) == 2


def test_match_pool_size_reported():
    store = MemoryStore()
    ingest_profile_v4(store, "seeker", _profile("意志"))
    _seed_pool(store, 3)
    out = match_v4(store, "seeker")
    assert out["pool_size"] == 3


# ── 跨プール禁止（I章）─────────────────────────────────────────────────────────
def test_shortlist_isolates_model_tag():
    """別 model_tag の候補は shortlist に混ざらない。"""
    store = MemoryStore()
    ingest_profile_v4(store, "seeker", _profile("意志"), model_tag="model-A")
    ingest_profile_v4(store, "same", _profile("候補A"), model_tag="model-A")
    ingest_profile_v4(store, "other", _profile("候補B"), model_tag="model-B")
    out = match_v4(store, "seeker", model_tag="model-A")
    ids = {r["candidate_id"] for r in out["results"]}
    assert "same" in ids
    assert "other" not in ids, "別 model_tag は跨プール禁止（I章）"
    assert out["pool_size"] == 1


# ── _union_shortlist 直接 ─────────────────────────────────────────────────────
def test_union_shortlist_merges_paths():
    """3経路の近傍 union を返す。"""
    e1 = [1.0] + [0.0] * (SHORT_DIM - 1)
    e2 = [0.0, 1.0] + [0.0] * (SHORT_DIM - 2)
    seeker = {k: e1 for k in ("necessity_q_256", "will_sym_256", "will_pas_256",
                              "state_pas_256")}
    cands = {
        "near": {"state_pas_256": e1, "will_sym_256": e1, "will_pas_256": e1},
        "far":  {"state_pas_256": e2, "will_sym_256": e2, "will_pas_256": e2},
    }
    chosen = _union_shortlist(seeker, cands, k=1)
    assert "near" in chosen


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\nStep 6 DoD テスト: {len(tests)} 件 全 PASS")
