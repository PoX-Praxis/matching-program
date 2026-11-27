"""指示書84 順1 — explain-v07 を本番の既定にする・生成は許可リストの組だけ・費用の歯止め・作り直しは 1 回だけ。テスト 596〜600。
架空の人だけ。モデルは動かさない（偽の推論先と偽の Jobs の API）。

596 版：POX_EXPLAIN_VERSION が無いときは explain-v07（鍵の頭 explain-v07:）。explain-v06・p81 は残る
597 許可リスト（POX_EXPLANATION_ALLOWLIST。表示のフラグと同じもの）の組だけを積み、作る。前に積んだ許可リストに無い組は
    作らず（skipped・not_allowlisted）、ジョブも起こさない。入力の無い人がいる組でもジョブを起こさない
598 費用の歯止め：許可が無い・1 か月の上限が無い（0）・今日（日本の暦）もう 1 回起動した・今月の上限に当たった、のどれかなら
    起動しない（予定に残し、まとめて作る回の記録に理由を書く）。起動の行は起動の前に書く。日本の暦の日と月で数える
599 1 回のジョブ：割り当て待ちの上限 2 時間（超えたら取り消す）・動き出してから 55 分でジョブの中で止まる・費用の上限 ≤ 5 ドル
600 作り直し：どちらかが登録し直したら、その組を 1 回だけ積む（二人とも・何度でも 1 行。作っている途中なら終わった後に 1 回）。
    入力が変わらなければ積まない。v4 の人の登録・編集でも積む。v5 の確定はベクトルの作成に失敗しても積む
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import explain_queue as Q  # noqa: E402
import explain_service as XSV  # noqa: E402
import explanation as X  # noqa: E402
from explain_pipeline import hfjobs as HJ  # noqa: E402
from explain_pipeline import prompts as PR  # noqa: E402
from explain_pipeline import runtime as RT  # noqa: E402
from explain_pipeline import synthetic as SY  # noqa: E402
from test_t81a3_v06 import Rec  # noqa: E402

DRAIN = "d" * 40


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in (RT.VERSION_ENV, RT.CELLS_ENV, RT.BOUND_ENV, RT.STRIP_ENV, *RT.PLACEMENT_ENV.values(),
              XSV.JOBS_OK_ENV, XSV.MONTHLY_ENV, XSV.BACKEND_ENV, "POX_EXPLANATION_ALLOWLIST"):
        monkeypatch.delenv(n, raising=False)
    XSV._CFG = None
    yield
    XSV._CFG = None


@pytest.fixture
def db():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    import schema
    schema.init(p)
    return p


@pytest.fixture
def people():
    sp = SY.pairs()[0]
    return {"x1": sp["a"], "x2": sp["b"]}


def _ctx(people):
    return XSV.Context(lambda s: people.get(s), {"x1": "架空P1", "x2": "架空P2"}.get)


class FakeSession:
    """偽の起動（with の中で偽の推論先を返す）。起こした回数を数える。"""

    def __init__(self, made):
        self.made = made
        self.record = {"job_id": f"job{len(made) + 1}", "flavor": "h200", "stage": "RUNNING", "seconds": 1.0, "cost_usd_est": 0.001}

    def __enter__(self):
        self.made.append(self)
        return Rec()

    def __exit__(self, *exc):
        return False


def _hf(monkeypatch, monthly="31"):
    monkeypatch.setenv(XSV.BACKEND_ENV, "hf_jobs")
    monkeypatch.setenv(XSV.JOBS_OK_ENV, "1")
    monkeypatch.setenv("POX_EXPLANATION_ALLOWLIST", "x1+x2")
    if monthly is not None:
        monkeypatch.setenv(XSV.MONTHLY_ENV, monthly)


def _batches(db):
    from db_connect import get_connection
    with get_connection(db) as con:
        rows = con.execute("SELECT record_json FROM explanation_batches ORDER BY started_at").fetchall()
    return [json.loads(r[0]) for r in rows if r[0]]


# ── 596 版 ─────────────────────────────────────────────────────────────────────────────
def test_t596_default_is_v07(monkeypatch):
    assert RT.DEFAULT_VERSION == "explain-v07" and RT.version_of() == "explain-v07" and RT.version_of({}) == "explain-v07"
    cfg = XSV.runtime()
    assert RT.version_id(cfg) == "explain-v07" and cfg["prompts"]["select_sha256"] == PR.SELECT_SHA256_V03 and RT.strip_label_words(cfg)
    assert XSV.version_key().startswith("explain-v07:331fa7d5")                            # 指示書83 の版の鍵のまま
    for v, head in (("explain-v06", "explain-v06:38512d95"), ("p81", "p81:")):              # v06・p81 は残る
        assert RT.version_key(RT.load(version=v), X.SCHEMA_VERSION, X.RELATION_RULE_VERSION).startswith(head)
    monkeypatch.setenv(XSV.BACKEND_ENV, "hf_jobs")
    monkeypatch.setenv(XSV.HF_TOKEN_ENV, "hf_x")
    st = XSV.startup_check()
    assert st["version"].startswith("explain-v07:") and st["jobs_per_day"] == 1 and st["jobs_per_month"] == 0
    monkeypatch.setenv(XSV.MONTHLY_ENV, "31")
    assert XSV.startup_check()["jobs_per_month"] == 31
    for bad in ("", "x", "-3"):
        monkeypatch.setenv(XSV.MONTHLY_ENV, bad)
        assert XSV.monthly_limit() == 0                                                     # 読めなければ 0（起動しない）


# ── 597 許可リストの組だけ ──────────────────────────────────────────────────────────────
def test_t597_allowlist_only(monkeypatch, db, people):
    _hf(monkeypatch)
    assert XSV.enqueue_pair("x1", "x3", db_path=db) is None and Q.row("x1|x3", db) is None   # 許可リストに無い組は積まない
    assert XSV.enqueue_for("x3", db_path=db) == 0 and Q.counts(db) == {}
    Q.enqueue("x1", "x3", db_path=db)                                                        # 前に積んだ行が残っていても
    made = []
    res = XSV.drain(_ctx(people), db_path=db, session_factory=lambda c: FakeSession(made))
    assert res["pairs"] == 0 and not made                                                    # 作らず、ジョブも起こさない
    assert Q.row("x1|x3", db)["status"] == "skipped" and Q.row("x1|x3", db)["reason"] == "not_allowlisted"
    assert Q.launches("hf_jobs", db_path=db)["today"] == 0
    # 入力の無い人がいる組：ジョブを起こさない（make_pair の skipped のために起動しない）
    assert XSV.enqueue_pair("x1", "x2", db_path=db) == "queued"
    res = XSV.drain(_ctx({"x1": people["x1"]}), db_path=db, session_factory=lambda c: FakeSession(made))
    assert res["pairs"] == 0 and not made and Q.row("x1|x2", db)["reason"] == "inputs"
    res = XSV.drain(_ctx({"x1": people["x1"]}), db_path=db, verify=True, session_factory=lambda c: FakeSession(made))
    assert res["pairs"] == 0 and not made                                                    # 確かめ（verify）でも起動しない
    # 許可リストの組は作る
    assert XSV.enqueue_pair("x2", "x1", db_path=db) == "queued"
    res = XSV.drain(_ctx(people), db_path=db, session_factory=lambda c: FakeSession(made))
    assert res["made"] == 1 and len(made) == 1 and Q.row("x1|x2", db)["status"] == "done"


def test_t597_drain_route_enqueue_allowlist_only(monkeypatch):
    import app as appmod
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    import schema
    schema.init(appmod.DB)
    appmod.app.config["TESTING"] = True
    monkeypatch.setenv(XSV.BACKEND_ENV, "hf_jobs")
    monkeypatch.setenv("POX_EXPLANATION_ALLOWLIST", "x1+x2")
    monkeypatch.setenv(XSV.DRAIN_TOKEN_ENV, DRAIN)
    monkeypatch.setattr(appmod, "_explain_input", lambda s: None)
    Q.enqueue("x1", "x9", db_path=appmod.DB)
    c = appmod.app.test_client()
    r = c.post("/ops/explanations/drain", headers={XSV.DRAIN_HEADER: DRAIN}, json={"enqueue": True})
    assert r.status_code == 202 and r.get_json()["queued"] == 1
    assert Q.row("x1|x9", appmod.DB)["status"] == "skipped" and Q.launches("hf_jobs", db_path=appmod.DB)["today"] == 0
    st = c.get("/ops/explanations/status", headers={XSV.DRAIN_HEADER: DRAIN}).get_json()
    assert st["jobs"] == {"permitted": False, "today": 0, "per_day": 1, "month": 0, "per_month": 0, "gate": "jobs_not_permitted"}


# ── 598 費用の歯止め ────────────────────────────────────────────────────────────────────
def test_t598_daily_and_monthly_limits(monkeypatch, db, people):
    _hf(monkeypatch, monthly=None)
    made = []
    factory = lambda c: FakeSession(made)  # noqa: E731
    XSV.enqueue_pair("x1", "x2", db_path=db)
    res = XSV.drain(_ctx(people), db_path=db, session_factory=factory)
    assert res["reason"] == "monthly_limit_unset" and not made and Q.row("x1|x2", db)["status"] == "pending"
    monkeypatch.setenv(XSV.MONTHLY_ENV, "31")
    res = XSV.drain(_ctx(people), db_path=db, session_factory=factory)
    assert res["made"] == 1 and len(made) == 1 and res["job"]["job_id"] == "job1"
    n = Q.launches("hf_jobs", db_path=db)
    assert n == {"today": 1, "month": 1}
    XSV.enqueue_pair("x1", "x2", db_path=db)                                             # 同じ日の 2 回目は起動しない
    res = XSV.drain(_ctx(people), db_path=db, session_factory=factory)
    assert res["reason"] == "daily_limit" and len(made) == 1 and Q.row("x1|x2", db)["status"] == "pending"
    assert res["jobs"]["today"] == 1 and res["jobs"]["gate"] == "daily_limit"
    res = XSV.drain(_ctx(people), db_path=db, verify=True, session_factory=factory)        # 作り直しの確かめも同じ上限
    assert res["reason"] == "daily_limit" and len(made) == 1
    reasons = [b.get("reason") for b in _batches(db)]                                    # まとめて作る回の記録に理由を書く
    assert reasons.count("monthly_limit_unset") == 1 and reasons.count("daily_limit") == 1


def test_t598_counts_by_japan_calendar(monkeypatch, db):
    _hf(monkeypatch, monthly="3")
    utc = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)  # noqa: E731
    Q.record_launch("hf_jobs", now=utc("2026-11-26T14:59:59"), db_path=db)               # 日本の 11/26 23:59:59
    assert XSV.launch_gate(db, now=utc("2026-11-26T14:59:59")) == "daily_limit"
    assert XSV.launch_gate(db, now=utc("2026-11-26T15:00:00")) is None                    # 日本の 11/27 0:00 は次の日
    Q.record_launch("hf_jobs", now=utc("2026-11-01T00:00:00"), db_path=db)
    Q.record_launch("hf_jobs", now=utc("2026-10-31T15:30:00"), db_path=db)               # 日本の 11/1（10 月ではない）
    assert Q.launches("hf_jobs", now=utc("2026-11-27T00:00:00"), db_path=db) == {"today": 0, "month": 3}
    assert XSV.launch_gate(db, now=utc("2026-11-27T00:00:00")) == "monthly_limit"
    assert XSV.launch_gate(db, now=utc("2026-11-30T15:00:00")) is None                    # 日本の 12/1 は次の月
    Q.record_launch("local_http", now=utc("2026-11-27T00:00:00"), db_path=db)            # PC（費用 0）は数えない
    assert Q.launches("hf_jobs", now=utc("2026-11-27T00:00:00"), db_path=db)["month"] == 3


# ── 599 1 回のジョブの上限 ──────────────────────────────────────────────────────────────
class StuckJobs:
    """割り当てが来ない（ずっと SCHEDULING）偽の Jobs の API。"""

    def __init__(self):
        self.calls = []

    def run(self, spec):
        self.calls.append(("run", spec["flavor"]))
        return {"id": "jobS", "status": {"stage": "SCHEDULING"}}

    def inspect(self, job_id):
        return {"id": job_id, "status": {"stage": "SCHEDULING"}}

    def cancel(self, job_id):
        self.calls.append(("cancel", job_id))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_t599_job_caps(monkeypatch, db, people):
    cfg = RT.load()
    h = cfg["hf_jobs"]
    assert h["flavor"] == "h200" and h["schedule_timeout_seconds"] == 7200 and h["max_run_seconds"] == 3300
    spec = HJ.job_spec(cfg, "k")
    assert spec["flavor"] == "h200" and spec["timeoutSeconds"] == 3 * 3600 and "timeout 3300 " in spec["command"][2]
    assert HJ.max_cost(cfg) == round(3300 / 3600 * 5.0, 3) and HJ.max_cost(cfg) <= 5.0
    _hf(monkeypatch)
    XSV.enqueue_pair("x1", "x2", db_path=db)
    clock, apis = Clock(), []

    def factory(c):
        api = StuckJobs()
        apis.append(api)
        return HJ.Session(c, api, clock=clock, sleep=clock.sleep, poll=60, key="k" * 40)
    res = XSV.drain(_ctx(people), db_path=db, session_factory=factory)
    assert res["reason"] == "TimeoutError" and res["job"]["stopped"] == "scheduling_timeout"
    assert apis[0].calls == [("run", "h200"), ("cancel", "jobS")]                        # 取り消して止める（ほかの GPU に替えない）
    assert 7200 <= clock.t - 1000.0 <= 7200 + 60 and res["job"]["cost_usd_est"] == 0     # 割り当て待ちは費用に数えない
    assert Q.row("x1|x2", db)["status"] == "pending"                                     # 予定に残す
    assert Q.launches("hf_jobs", db_path=db)["today"] == 1                               # 起動は数える（同じ日はもう起動しない）
    assert XSV.drain(_ctx(people), db_path=db, session_factory=factory)["reason"] == "daily_limit" and len(apis) == 1


# ── 600 作り直しは 1 回だけ ──────────────────────────────────────────────────────────────
@pytest.fixture
def app_env(monkeypatch, people):
    import app as appmod
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    import schema
    schema.init(appmod.DB)
    appmod.app.config["TESTING"] = True
    _hf(monkeypatch)
    monkeypatch.delenv(XSV.JOBS_OK_ENV)                                    # 積むだけ（ジョブは起こさない）
    cur = {k: json.loads(json.dumps(v)) for k, v in people.items()}
    monkeypatch.setattr(appmod, "_explain_input", lambda s: cur.get(s))
    monkeypatch.setattr(appmod, "_plain_name", {"x1": "架空P1", "x2": "架空P2"}.get)
    return appmod, cur


def test_t600_reregistration_queues_once(monkeypatch, app_env):
    appmod, cur = app_env
    db = appmod.DB
    appmod._explanation_trigger("x1")
    appmod._explanation_trigger("x1")
    appmod._explanation_trigger("x2")                                     # 二人とも・何度でも、作るのは 1 回
    assert Q.counts(db) == {"pending": 1} and Q.row("x1|x2", db)["attempts"] == 0
    monkeypatch.setenv(XSV.JOBS_OK_ENV, "1")
    made = []
    assert XSV.drain(appmod._explain_ctx(), db_path=db, session_factory=lambda c: FakeSession(made))["made"] == 1
    appmod._explanation_trigger("x1")                                     # 入力が変わらなければ積まない（ジョブの費用をかけない）
    assert Q.row("x1|x2", db)["status"] == "done"
    cur["x1"]["関心"] = [{"文": "架空の関心の文を足した。"}]                 # 登録し直し（説明の入力が変わる）
    appmod._explanation_trigger("x1")
    appmod._explanation_trigger("x1")
    appmod._explanation_trigger("x2")
    assert Q.counts(db) == {"pending": 1}
    assert Q.claim("x1|x2", db_path=db)                                   # 作っている途中に、もう一度登録し直した
    cur["x2"]["関心"] = [{"文": "架空の別の関心の文。"}]
    appmod._explanation_trigger("x2")
    appmod._explanation_trigger("x2")
    assert Q.row("x1|x2", db)["again"] is True
    assert Q.finish("x1|x2", "done", db_path=db) == "pending"             # 終わった後に 1 回だけ作り直す
    assert Q.claim("x1|x2", db_path=db) and Q.finish("x1|x2", "done", db_path=db) == "done"
    assert Q.counts(db) == {"done": 1} and len(made) == 1


def test_t600_v4_and_v5_paths_trigger(monkeypatch, app_env):
    appmod, _ = app_env
    calls = []
    monkeypatch.setattr(appmod, "_explanation_trigger", lambda sid: calls.append(sid))
    monkeypatch.setattr(appmod, "_is_v5_subject", lambda sid: False)

    class Store:
        def set_generation_status(self, *a, **k):
            pass
    monkeypatch.setattr(appmod, "_v4_store", lambda: Store())

    def boom(*a, **k):
        raise RuntimeError("埋め込みに届かない")
    monkeypatch.setattr(appmod, "_run_with_retry", boom)
    appmod._v4_async_job("x1", {}, {"necessity_text": "架空"}, is_fallback=False, publish_ledger=False)
    assert calls == ["x1"]                                                # v4 の登録・編集でも積む（ベクトルに失敗しても）
    monkeypatch.setattr(appmod, "_is_v5_subject", lambda sid: True)
    appmod._v4_async_job("x1", {}, {"necessity_text": "架空"}, is_fallback=False, publish_ledger=False)
    assert calls == ["x1"]                                                # v5 の人はここでは積まない（下で積む）
    import v5
    monkeypatch.setattr(v5, "live_necessities_v5", boom)
    appmod._v5_sentence_job("x2", {"offer_id": "o1"})
    assert calls == ["x1", "x2"]                                          # v5 の確定：ベクトルの作成に失敗しても積む
