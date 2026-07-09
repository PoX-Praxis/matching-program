"""
Step 6 DoD 検証テスト — プラットフォーム統合 / v4 取り込み・照合（F章）

束2a 更新:
  - 256 shortlist 廃止 → フル次元総当たり（match_v4 は同 model_tag・is_active 全件を rank）
  - profile_vectors は full 4ベクトルのみ保存（_256 は廃止）
  - derived_necessity は履歴保存（MemoryStore.get_necessity が最新を返す）

DoD（Postgres 無しで MemoryStore により全オーケストレーションを検証）:
  - ingest_profile_v4: raw 保存 / redacted 保存 / PII status / ② 必要像生成 / 4ベクトル保存
  - I章: raw を embedding/LLM に直接渡さない（防御 redact）
  - match_v4: ランキング生成 / γ,p,α,β が derived_necessity から流れる / ledger_v4 監査記録
  - 跨プール禁止: 照合は同 model_tag のみ（別 model_tag を混ぜない）
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db_v4 import ingest_profile_v4, match_v4, MemoryStore
from embedding_config import MODEL_TAG, FULL_DIM
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
    assert saved["will_text"] == "農家と店をつなぎたい 連絡 a@b.com"
    assert saved["pii_redaction_status"] == "structural_done"
    assert "supporting_redacted" in saved


def test_ingest_generates_necessity_owned_by_two():
    store = MemoryStore()
    ingest_profile_v4(store, "u1", _profile("意志", want="必要な相手"))
    nec = store.get_necessity("u1", MODEL_TAG)
    for k in ("necessity_text", "gate_s", "gate_u", "gamma", "p_sharpness",
              "alpha", "beta", "src_input_hash", "generator_prompt_version"):
        assert k in nec
    assert nec["gamma"] == compute_gamma(nec["gate_s"], nec["gate_u"], GAMMA_MAX)


def test_ingest_saves_full_vectors_correct_dims():
    """束2a: 保存は full 4ベクトルのみ（_256 は保存しない）。"""
    store = MemoryStore()
    ingest_profile_v4(store, "u1", _profile("意志"))
    v = store.vectors[("u1", MODEL_TAG)]
    assert set(v.keys()) == {"will_symmetric", "will_passage", "state_passage", "necessity_query"}
    for k in v:
        assert len(v[k]) == FULL_DIM


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
    assert store.get_necessity("u1", MODEL_TAG)["beta"] == 2.0


def test_ingest_sets_status_ready():
    store = MemoryStore()
    ingest_profile_v4(store, "u1", _profile("意志"))
    assert store.get_profile_status("u1")["generation_status"] == "ready"


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
    assert out["seeker_id"] == "seeker" and out["model_tag"] == MODEL_TAG
    assert len(out["results"]) >= 1
    for r in out["results"]:
        assert {"candidate_id", "score", "attribution"} <= set(r)
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
    assert ev["seeker_id"] == "seeker" and ev["event"] == "match_ranked"
    for k in ("score", "limiting_axis", "gamma", "alpha", "beta", "model_tag"):
        assert k in ev["payload"]


def test_match_no_ledger_when_disabled():
    store = MemoryStore()
    ingest_profile_v4(store, "seeker", _profile("つなぎたい"))
    _seed_pool(store, 2)
    match_v4(store, "seeker", write_ledger=False)
    assert store.ledger == []


def test_match_gamma_flows_from_necessity():
    store = MemoryStore()
    def gamma_zero(inputs):
        return {"necessity_text": "必要", "gate_s": 0.0, "gate_u": 0.0,
                "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0, "evidence_span": ""}
    ingest_profile_v4(store, "seeker", _profile("意志"), generator_fn=gamma_zero)
    _seed_pool(store, 2)
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


def test_match_isolates_model_tag():
    """別 model_tag の候補は照合に混ざらない（跨プール禁止 I章）。"""
    store = MemoryStore()
    ingest_profile_v4(store, "seeker", _profile("意志"), model_tag="model-A")
    ingest_profile_v4(store, "same", _profile("候補A"), model_tag="model-A")
    ingest_profile_v4(store, "other", _profile("候補B"), model_tag="model-B")
    out = match_v4(store, "seeker", model_tag="model-A")
    ids = {r["candidate_id"] for r in out["results"]}
    assert "same" in ids
    assert "other" not in ids, "別 model_tag は跨プール禁止（I章）"
    assert out["pool_size"] == 1
