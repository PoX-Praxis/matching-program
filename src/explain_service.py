"""説明の生成を本番につなぐ（指示書81 §1〜§3・段階3b）。

流れ（scripts/explain_pipeline。ラベル v0.3 → 確かめ C8 → 選ぶ・まとめる v0.1 → カード）を、本番の予定（explain_queue）と
推論先（POX_EXPLAIN_BACKEND）につなぐ。
- hf_jobs：たまった組を、1 回の起動でまとめて作る（Hugging Face Jobs のジョブで llama-server を起こし、口を公開して呼ぶ）。
  間隔は 1 日 1 回（予定が無ければ起動しない）
- local_http：予定が入ったら、すぐ作る（PC の llama-server。トンネルの先）。PC が止まっていれば予定に残し、戻ったら作る
- 推論先が無い（未設定）なら、作らない
- 1 組ずつ・二つの向きも順に（報告80）。候補は A だけ（config/explain_runtime.toml の cells）
- 作れていない組は、画面に出さない（「準備中」とも出さない）。表示はいまの二人の読む欄（入力）と、いまの版のものだけ
- 記録（予定の行・まとめて作る回・向きのレコードの meta）には、数と時刻と番号と版だけを残す。文は残さない
- 再現性の合格（決定ログ 2026-11-24）：同じ入力・同じ版で作り直したとき、pair_digest と目的の状態が同じこと（verify）
"""
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import explain_queue as Q  # noqa: E402
import explanation as X  # noqa: E402
import explanation_store as S  # noqa: E402

BACKENDS = ("hf_jobs", "local_http")
BACKEND_ENV = "POX_EXPLAIN_BACKEND"
ENDPOINT_ENV, ENDPOINT_KEY_ENV = "POX_EXPLAIN_ENDPOINT", "POX_EXPLAIN_ENDPOINT_KEY"
HF_TOKEN_ENV, HF_NS_ENV = "POX_HF_TOKEN", "POX_HF_NAMESPACE"
GPU_ENV = "POX_EXPLAIN_GPU"                                # PC の GPU の名前（版の記録用。local_http のとき）
INTERVAL_ENV = "POX_EXPLAIN_INTERVAL_HOURS"                # hf_jobs の間隔（既定 24 時間）
DRAIN_TOKEN_ENV, DRAIN_HEADER, TOKEN_MIN = "POX_EXPLAIN_DRAIN_TOKEN", "X-Explain-Drain-Token", 32
FLAVOR_GPU = {"h200": "NVIDIA H200", "h100": "NVIDIA H100 80GB", "a100-large": "NVIDIA A100 80GB", "l40sx1": "NVIDIA L40S"}
PIPELINE_MARK = "p81"
_LOCK = threading.Lock()
_CFG = None


def backend():
    v = (os.environ.get(BACKEND_ENV) or "").strip().lower()
    return v if v in BACKENDS else None


def runtime():
    """設定（config/explain_runtime.toml）。一度読んだら使い回す。"""
    global _CFG
    if _CFG is None:
        from explain_pipeline import runtime as RT
        _CFG = RT.load()
    return _CFG


def version_key(cfg=None):
    from explain_pipeline import runtime as RT
    return RT.version_key(cfg or runtime(), X.SCHEMA_VERSION, X.RELATION_RULE_VERSION)


def startup_check():
    """起動のとき：推論先が設定されていれば、設定のファイルとプロンプトの sha256・必要な環境変数があるかを確かめる。
    足りなければ起動しない。返すのは有無と版の頭だけ（鍵・URL は出さない）。"""
    b = backend()
    if not b:
        return {"backend": "off"}
    try:
        cfg = runtime()
        from explain_pipeline import prompts as PR
        if (cfg["prompts"]["label_sha256"], cfg["prompts"]["select_sha256"]) != (PR.LABEL_SHA256, PR.SELECT_SHA256):
            raise ValueError("設定のプロンプトの sha256 が docs/prompts/ と違う")
        PR.template(PR.LABEL_FILE, PR.LABEL_SHA256)
        PR.template(PR.SELECT_FILE, PR.SELECT_SHA256)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"[FATAL] {BACKEND_ENV}={b} ですが、説明の生成の設定が読めません（{type(e).__name__}: {e}）。")
    need = {"hf_jobs": (HF_TOKEN_ENV,), "local_http": (ENDPOINT_ENV, ENDPOINT_KEY_ENV)}[b]
    missing = [n for n in need if not (os.environ.get(n) or "").strip()]
    if missing:
        raise SystemExit(f"[FATAL] {BACKEND_ENV}={b} ですが {', '.join(missing)} が未設定です（Render の環境変数）。")
    return {"backend": b, "version": version_key(cfg)[:16], "cells": cfg["pipeline"]["cells"],
            "drain_route": "開いている" if drain_token_ok(os.environ.get(DRAIN_TOKEN_ENV)) else "閉じている"}


def drain_token_ok(value):
    want = os.environ.get(DRAIN_TOKEN_ENV) or ""
    if len(want) < TOKEN_MIN or not value:
        return False
    return hmac.compare_digest(str(value), want)


# ── 入力・版・as-of ──────────────────────────────────────────────────────────────
def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def tag(person):
    """人の入力（説明が読む欄）の内容ハッシュ。as-of に使う（どちらかが更新したら変わる）。"""
    return "h:" + hashlib.sha256(_canon(person).encode("utf-8")).hexdigest()[:40]


def has_purposes(person):
    from explain_pipeline import fields as FL
    return FL.has_purposes(person)


def as_of(need_person, offer_person):
    """向き（need × offer）の as-of（カードの組み立てと同じ形）。"""
    def side(p):
        t = tag(p)
        return {"profile": t, "necessity": t if has_purposes(p) else None}
    return {"need": side(need_person), "offer": side(offer_person)}


def generation(cfg):
    m = cfg["model"]
    from explain_pipeline import runtime as RT
    return {"model": f"{m['repo']}/{m['file']}", "revision": m["revision"], "decoding": RT.decoding(cfg)}


class Context:
    """本番の入力の引き方（app から渡す）。inputs_of(subject) → 人の入力（同意していない・無いなら None）。
    name_of(subject) → 呼び方（表示名）。"""

    def __init__(self, inputs_of, name_of):
        self.inputs_of, self.name_of = inputs_of, name_of


# ── 1 組を作る ───────────────────────────────────────────────────────────────────
def statuses(card):
    return {"purposes": [p["status"] for p in card["purposes"]], "reverse_purposes": [p["status"] for p in card["reverse_purposes"]]}


def signature(card, applicant, recipient):
    """再現性の合格の確かめ（決定ログ 2026-11-24）に使う値：pair_digest（鍵があるとき）と、組の形のハッシュ（鍵が無くても）。"""
    shape = [[p["ref"], p["status"], sorted((e["need_ref"], e["offer_ref"], e["relation"]) for e in p["elements"])]
             for k in ("purposes", "reverse_purposes") for p in card[k]]
    out = {"shape": hashlib.sha256(_canon(shape).encode("utf-8")).hexdigest()}
    try:
        import explanation_agreement as A
        out["pair_digest"] = A.pair_digest(A.a1_object(card, applicant, recipient))[0]
    except Exception:  # noqa: BLE001 — 鍵が無い（POX_EXPLANATION=off の本番）ときは形のハッシュだけ
        out["pair_digest"] = None
    return out


def make_pair(a, b, ctx, ep, cfg):
    """組 1 つ（a・b）を作る。戻り値: {"status", "reason"?, "cards"?, "record"?, "people"?}。文は cards にだけある。"""
    from explain_pipeline import prompts as PR
    from explain_pipeline import runner as RN
    from explain_pipeline import runtime as RT
    x, y = Q.ordered(a, b)
    px, py = ctx.inputs_of(x), ctx.inputs_of(y)
    if not px or not py:
        return {"status": "skipped", "reason": "inputs"}
    nx, ny = ctx.name_of(x) or "", ctx.name_of(y) or ""
    spec = {"id": Q.pair_key(x, y), "a": px, "b": py, "names": [n for n in (nx, ny) if n],
            "people": {"A": {"name": nx, "tag": tag(px)}, "B": {"name": ny, "tag": tag(py)}}, "generation": generation(cfg)}
    t0 = time.time()
    out, rec = RN.run_pair(spec, ep, direction_parallel=False, max_tokens=int(cfg["generation"]["max_tokens"]), origin=t0,
                           clock=time.time, cells=RT.cells(cfg), label_sha=PR.LABEL_SHA256, select_sha=PR.SELECT_SHA256)
    errors = [q["error"] for q in rec["requests"] if q["error"]]
    if errors:
        return {"status": "pending", "reason": errors[0]}          # 推論先に届かない・切れた：予定に残す
    for q in rec["requests"]:                                     # JSON の部分と考える部分のトークン（時間を測った後に数える）
        src = out["label" if q["stage"] == "label" else "select"][q["id"]]
        for key, part in (("content_tokens", "text"), ("reasoning_tokens_counted", "reasoning")):
            try:
                q[key] = ep.count_tokens(src[part]) if src.get(part) else 0
            except Exception:  # noqa: BLE001
                q[key] = None
    rec.pop("pair", None)
    cards = {x: out["cards"]["A"], y: out["cards"]["B"]}
    return {"status": "done", "cards": cards, "record": rec, "people": {x: px, y: py}, "order": (x, y)}


def store(res, cfg, version_record, db_path="pox.db"):
    """作ったカード 2 枚を、向きのレコード 2 つにして保存する（source='generated'・版の鍵 p81:…）。"""
    x, y = res["order"]
    vk = version_key(cfg)
    for viewer, target in ((x, y), (y, x)):
        card = res["cards"][viewer]
        fwd, _rev = X.split_card(card)
        sig = signature(card, viewer, target)
        meta = {"pipeline": PIPELINE_MARK, "versions": version_record, "record": res["record"],
                "statuses": statuses(card), "signature": sig}
        gk = hashlib.sha256(_canon({"dir": [viewer, target], "as_of": fwd["as_of"], "v": vk}).encode("utf-8")).hexdigest()
        S.save_generated(viewer, target, fwd, gk, vk, meta, need_has_purposes=has_purposes(res["people"][viewer]), db_path=db_path)


def stored_meta(viewer, target, ctx, cfg, db_path="pox.db"):
    """いまの入力・いまの版の、保存した向きの meta（無ければ None）。"""
    pv, pt = ctx.inputs_of(viewer), ctx.inputs_of(target)
    if not pv or not pt:
        return None
    row = S.generated_direction(viewer, target, S.as_of_digest({"as_of": as_of(pv, pt)}), db_path=db_path)
    if not row or row["version_key"] != version_key(cfg) or row["superseded"]:
        return None
    return row


def direction_getter(ctx, db_path="pox.db"):
    """card_for に渡す向きの引き方。いまの二人の入力・いまの版で作った向きだけを返す（無ければ出さない。その場で作らない）。"""
    def get(need, offer):
        try:
            row = stored_meta(need, offer, ctx, runtime(), db_path)
        except Exception:  # noqa: BLE001
            row = None
        return (row["direction"], row["need_has_purposes"]) if row else (None, None)
    return get


def compare(res, ctx, cfg, db_path="pox.db"):
    """作り直した結果（保存しない）と、保存してあるものを比べる。pair_digest と目的の状態が同じなら same。番号と真偽だけを返す。"""
    x, y = res["order"]
    out = {}
    for viewer, target in ((x, y), (y, x)):
        row = stored_meta(viewer, target, ctx, cfg, db_path)
        if not row:
            out[f"{viewer}>{target}"] = {"stored": False}
            continue
        new_sig, new_st = signature(res["cards"][viewer], viewer, target), statuses(res["cards"][viewer])
        old = row["meta"]
        out[f"{viewer}>{target}"] = {"stored": True,
                                     "pair_digest_same": (old.get("signature") or {}).get("pair_digest") == new_sig["pair_digest"],
                                     "shape_same": (old.get("signature") or {}).get("shape") == new_sig["shape"],
                                     "statuses_same": old.get("statuses") == new_st}
    out["same"] = all(v.get("stored") and v["pair_digest_same"] and v["statuses_same"] for k, v in out.items() if k != "same")
    return out


# ── 予定を積む・まとめて作る ─────────────────────────────────────────────────────────
def enqueue_for(subject, db_path="pox.db"):
    """登録・更新のきっかけ：その人が入っている許可リストの組を積む。推論先が無ければ何もしない。積んだ組の数を返す。"""
    if not backend():
        return 0
    n = 0
    for pair in S.allowlist(db_path):
        if subject in pair:
            a, b = sorted(pair)
            if Q.enqueue(a, b, db_path=db_path):
                n += 1
    return n


def _version_record(cfg, b, gpu, extra=None):
    from explain_pipeline import runtime as RT
    return RT.version_record(cfg, b, gpu=gpu, code_commit=os.environ.get("RENDER_GIT_COMMIT"), extra=extra)


def _items(verify, db_path):
    if not verify:
        return Q.pending(db_path)
    return [{"pair_key": Q.pair_key(*sorted(p)), "a": sorted(p)[0], "b": sorted(p)[1]} for p in sorted(S.allowlist(db_path), key=sorted)]


def _run_items(items, ctx, ep, cfg, b, gpu, extra, verify, db_path):
    made, results = 0, []
    for it in items:
        k = it["pair_key"]
        if not verify and not Q.claim(k, db_path=db_path):
            continue
        try:
            res = make_pair(it["a"], it["b"], ctx, ep, cfg)
        except Exception as e:  # noqa: BLE001
            res = {"status": "failed", "reason": type(e).__name__}
        if verify:
            results.append({"pair_key": k, "status": res["status"],
                            **({"compare": compare(res, ctx, cfg, db_path)} if res["status"] == "done" else {"reason": res.get("reason")})})
            continue
        if res["status"] == "done":
            store(res, cfg, _version_record(cfg, b, gpu, extra), db_path=db_path)
            made += 1
            digest = hashlib.sha256("".join(tag(p) for p in res["people"].values()).encode("utf-8")).hexdigest()[:40]
            Q.finish(k, "done", inputs_digest=digest, record=res["record"], db_path=db_path)
        else:
            Q.finish(k, res["status"], reason=res.get("reason"), db_path=db_path)
        results.append({"pair_key": k, "status": res["status"], **({"reason": res["reason"]} if res.get("reason") else {})})
    return made, results


def drain(ctx, db_path="pox.db", verify=False, backend_name=None, session_factory=None, endpoint=None, keepalive=None):
    """予定の組をまとめて作る（1 回の起動で、1 組ずつ）。verify=True は、許可リストの組を作り直して保存してあるものと比べるだけ
    （保存しない。PC に移したときの確かめ。§4-1 の 7）。戻り値は数と番号だけ。同時に 2 つは動かさない。"""
    if not _LOCK.acquire(blocking=False):
        return {"busy": True}
    try:
        b = backend_name or backend()
        if not b:
            return {"backend": "off"}
        Q.reset_stale(db_path=db_path)
        items = _items(verify, db_path)
        if not items:
            return {"backend": b, "pairs": 0, "made": 0}
        cfg = runtime()
        from explain_pipeline import endpoint as EP
        from explain_pipeline import hfjobs as HJ
        bid = Q.start_batch(b, db_path=db_path) if not verify else None
        rec = {"backend": b, "pairs": len(items), "verify": verify}
        ka = _KeepAlive(keepalive) if keepalive else None
        try:
            if b == "local_http":
                ep = endpoint or EP.from_runtime(cfg, os.environ.get(ENDPOINT_ENV, ""), os.environ.get(ENDPOINT_KEY_ENV))
                if not ep.healthy():
                    rec.update(made=0, reason="endpoint_down")             # PC が止まっている：予定に残す（戻ったら作る）
                    return rec
                made, results = _run_items(items, ctx, ep, cfg, b, os.environ.get(GPU_ENV) or None, None, verify, db_path)
            else:
                sess = (session_factory or _hf_session)(cfg)
                with sess as ep:
                    gpu = FLAVOR_GPU.get((cfg.get("hf_jobs") or {}).get("flavor"))
                    made, results = _run_items(items, ctx, ep, cfg, b, gpu, {"job_id": sess.record.get("job_id")}, verify, db_path)
                rec["job"] = {k: v for k, v in sess.record.items() if k in ("job_id", "flavor", "startup_seconds", "seconds", "cost_usd_est", "stage")}
            rec.update(made=made, results=results)
            return rec
        except Exception as e:  # noqa: BLE001 — ジョブが起きない・止まった：予定に残す
            for it in items:
                r = Q.row(it["pair_key"], db_path=db_path)
                if r and r["status"] == "running":
                    Q.finish(it["pair_key"], "pending", reason=type(e).__name__, db_path=db_path)
            rec.update(made=rec.get("made", 0), reason=type(e).__name__)
            return rec
        finally:
            if ka:
                ka.stop()
            if bid:
                Q.finish_batch(bid, rec["pairs"], rec.get("made", 0), {k: v for k, v in rec.items() if k != "results"}, db_path=db_path)
    finally:
        _LOCK.release()


def _hf_session(cfg):
    from explain_pipeline import hfjobs as HJ
    return HJ.Session(cfg, HJ.JobsApi(os.environ.get(HF_TOKEN_ENV, ""), namespace=os.environ.get(HF_NS_ENV) or None))


class _KeepAlive:
    """まとめて作るあいだ、本番の入れ物が止まらないように、数分ごとに自分の頁を 1 回読む（Render の無料の入れ物は、
    外からの要求が無いと止まる）。"""

    def __init__(self, url, every=240):
        self._stop = threading.Event()
        self.url, self.every = url, every
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.wait(self.every):
            try:
                urllib.request.urlopen(self.url, timeout=20).read(1)
            except Exception:  # noqa: BLE001
                pass

    def stop(self):
        self._stop.set()


def due(db_path="pox.db", now=None):
    """いま作るか：local_http は予定があれば。hf_jobs は予定があり、前の回から間隔（既定 24 時間）が過ぎていれば。"""
    b = backend()
    if not b or not Q.pending(db_path):
        return False
    if b == "local_http":
        return True
    last = Q.last_batch_started("hf_jobs", db_path=db_path)
    if not last:
        return True
    hours = float(os.environ.get(INTERVAL_ENV) or 24)
    now = now or datetime.now(timezone.utc)
    return now - datetime.fromisoformat(last) >= timedelta(hours=hours)


def start_scheduler(ctx, db_path="pox.db", every=600, keepalive=None):
    """裏で数分ごとに「いま作るか」を見て、作る（入れ物が起きているあいだだけ動く。止まっていた分は、起きた後に作る）。"""
    if not backend():
        return None

    def loop():
        while True:
            time.sleep(every)
            try:
                if due(db_path):
                    drain(ctx, db_path=db_path, keepalive=keepalive)
            except Exception:  # noqa: BLE001
                pass
    th = threading.Thread(target=loop, daemon=True, name="explain-scheduler")
    th.start()
    return th
