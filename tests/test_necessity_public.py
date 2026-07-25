"""
指示書11: 必要像の公開露出。

- get_public_necessity: 数値・evidence_span を出さず necessity_text のみ。日時閾値ゲート。
- get_owner_necessity: necessity_text＋evidence_span（数値は出さない）、閾値に関わらず。
- /api/profile は necessity_text のみマージ（数値・evidence_span なし）。
- /api/my/necessity は本人の text＋根拠。
- MemoryStore.get_necessity_public は公開3列のみ返す。
"""
import os, sys
from datetime import datetime, timezone, timedelta
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
from db_v4 import MemoryStore, MODEL_TAG, receive_profile_v4, GEN_READY
from necessity_gen import build_user_necessity

_NUMS = ("gate_s", "gate_u", "gamma", "p_sharpness", "alpha", "beta")


def _store_with(gen_at):
    store = MemoryStore()
    pin = {"will_text": "w", "state_have": "h", "state_can_type": "",
           "state_bound": "", "state_unsorted": "", "supporting_raw": {}}
    nec = build_user_necessity(
        {**pin, "supporting_redacted": {}},
        {"necessity_text": "現場を実装に翻訳できる開発者", "gate_s": 0.6, "gate_u": 0.3,
         "p_sharpness": -0.4, "alpha": 1.0, "beta": 2.0,
         "evidence_span": "一緒に背負える人がいい", "generator": "Claude"})
    receive_profile_v4(store, "u1", pin, nec, generation_status=GEN_READY)
    # MemoryStore の necessity 行に generated_at を注入（Postgres の TIMESTAMPTZ 相当）
    row = store.necessity[("u1", MODEL_TAG)][-1]
    row["generated_at"] = gen_at
    return store


def _patch(store):
    orig = (appmod.is_postgres, appmod._v4_store)
    appmod.is_postgres = lambda: True
    appmod._v4_store = lambda: store
    return orig


def _restore(orig):
    appmod.is_postgres, appmod._v4_store = orig


def _future():  return datetime.now(timezone.utc) + timedelta(days=1)
def _past():    return datetime.now(timezone.utc) - timedelta(days=3650)


# ── store 層: 公開列のみ ─────────────────────────────────────────────────────
def test_memory_store_public_cols_no_numbers():
    store = _store_with(_future())
    pub = store.get_necessity_public("u1", MODEL_TAG)
    assert set(pub.keys()) == {"necessity_text", "evidence_span", "generated_at"}
    for k in _NUMS:
        assert k not in pub


# ── get_public_necessity: 数値なし・necessity_textのみ・日時ゲート ────────────
def test_public_necessity_text_only_and_gated():
    orig = _patch(_store_with(_future()))   # 閾値より後に生成 → 公開
    try:
        pub = appmod.get_public_necessity("u1")
        assert pub == {"necessity_text": "現場を実装に翻訳できる開発者"}
        for k in _NUMS + ("evidence_span", "generator_name"):
            assert k not in pub
    finally:
        _restore(orig)


def test_public_necessity_hidden_for_old_rows():
    orig = _patch(_store_with(_past()))      # 閾値より前 → 既存扱いで非公開
    try:
        assert appmod.get_public_necessity("u1") is None
    finally:
        _restore(orig)


def test_public_necessity_none_when_absent():
    orig = _patch(MemoryStore())             # necessity 未生成
    try:
        assert appmod.get_public_necessity("u1") is None
    finally:
        _restore(orig)


# ── get_owner_necessity: text＋根拠、数値なし、閾値に関わらず ─────────────────
def test_owner_necessity_has_evidence_no_numbers_regardless_of_date():
    orig = _patch(_store_with(_past()))      # 古くても本人には見せる
    try:
        own = appmod.get_owner_necessity("u1")
        assert own["necessity_text"] == "現場を実装に翻訳できる開発者"
        assert own["evidence_span"] == "一緒に背負える人がいい"
        for k in _NUMS:
            assert k not in own
    finally:
        _restore(orig)


# ── HTTP: /api/profile は necessity_text のみ・数値/evidence_span なし ────────
def test_api_profile_merges_text_only():
    store = _store_with(_future())
    # profile_view を返せるよう get_profile_view をスタブ（seeker 非公開の pv）
    orig = (appmod.is_postgres, appmod._v4_store, appmod.get_profile_view)
    appmod.is_postgres = lambda: True
    appmod._v4_store = lambda: store
    appmod.get_profile_view = lambda uid, db_path=None: {"schema_version": "v4", "pursuing": "x"}
    try:
        data = appmod.app.test_client().get("/api/profile/u1").get_json()
        assert data.get("necessity_text") == "現場を実装に翻訳できる開発者"
        for k in _NUMS + ("evidence_span", "generator_name", "gate_s"):
            assert k not in data
    finally:
        appmod.is_postgres, appmod._v4_store, appmod.get_profile_view = orig


def test_api_my_necessity_owner_text_and_evidence():
    orig = _patch(_store_with(_past()))
    try:
        data = appmod.app.test_client().get("/api/my/necessity?id=u1").get_json()
        assert data["necessity_text"] and data["evidence_span"]
        for k in _NUMS:
            assert k not in data
    finally:
        _restore(orig)


def test_api_my_necessity_requires_id():
    assert appmod.app.test_client().get("/api/my/necessity").status_code == 400


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\n必要像公開テスト: {len(tests)} 件 全 PASS")
