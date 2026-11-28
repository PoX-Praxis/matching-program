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
- 版（指示書84）：本番の既定は explain-v07（vLLM v0.33.0・条件 B・候補 A・B・C・選ぶ段 v0.3・ラベルの語を消す）。
  explain-v06（選ぶ段 v0.1）と p81（llama.cpp）は POX_EXPLAIN_VERSION で選ぶ。HF Jobs のジョブ（費用がかかる）は、
  POX_EXPLAIN_JOBS_PERMITTED=1 のときだけ起動する（決定ログ 2026-11-24：KAORU が明示に許可したときだけ）。無ければ予定に残す
- 費用の歯止め（指示書84 §1-3）：ジョブは日本の暦で 1 日 1 回まで・1 か月は POX_EXPLAIN_JOBS_PER_MONTH 回まで（無ければ 0＝起動しない）。
  起動する前に起動の行を書いて数える。上限に当たったら起動せず、予定に残し、まとめて作る回の記録に理由を書く。
  1 回のジョブは、動き出してから [hf_jobs] max_run_seconds（55 分）で、ジョブの中で止まる
- 生成は許可リスト（POX_EXPLANATION_ALLOWLIST。表示のフラグと同じもの）の組だけ。許可リストに無い組は予定に積まず、
  前に積んだ行が残っていても作らない（skipped・not_allowlisted。ジョブも起こさない）
- 保存した出力を入れる（replay）：v06 の記録と、いまの二人の入力から描いたもの（入力・部品と候補・プロンプト）が同じときだけ、
  保存したラベル・選ぶ段の出力からカードを作り、版の鍵で入れる。違えば違う位置と種類だけを返し、入れない
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
JOBS_OK_ENV = "POX_EXPLAIN_JOBS_PERMITTED"               # HF Jobs のジョブを起動してよいか（KAORU の許可。"1" のときだけ）
MONTHLY_ENV = "POX_EXPLAIN_JOBS_PER_MONTH"                # 1 か月（日本の暦）に起動するジョブの上限（KAORU が決める。無ければ 0）
JOBS_PER_DAY = 1                                           # 1 日（日本の暦）に起動するジョブは 1 回まで（決定 2026-11-24）
FLAVOR_GPU = {"h200": "NVIDIA H200", "h100": "NVIDIA H100 80GB", "a100-large": "NVIDIA A100 80GB", "l40sx1": "NVIDIA L40S"}
PIPELINE_MARK = "p81"
_LOCK = threading.Lock()
_CFG = None


def backend():
    v = (os.environ.get(BACKEND_ENV) or "").strip().lower()
    return v if v in BACKENDS else None


def runtime():
    """選んだ版（POX_EXPLAIN_VERSION。既定 explain-v07）の設定。一度読んだら使い回す（版を変えたら読み直す）。"""
    global _CFG
    from explain_pipeline import runtime as RT
    if _CFG is None or RT.version_id(_CFG) != RT.version_of():
        _CFG = RT.load()
    return _CFG


def jobs_permitted():
    return (os.environ.get(JOBS_OK_ENV) or "").strip() == "1"


def monthly_limit():
    """1 か月に起動するジョブの上限（POX_EXPLAIN_JOBS_PER_MONTH）。無い・読めない・負なら 0（起動しない。閉じる側に倒す）。"""
    try:
        return max(0, int((os.environ.get(MONTHLY_ENV) or "").strip()))
    except ValueError:
        return 0


def launch_gate(db_path="pox.db", now=None):
    """いま HF Jobs のジョブを起動してよいか。よければ None、だめなら理由（jobs_not_permitted・monthly_limit_unset・
    daily_limit・monthly_limit）。数えるのは起動の行（日本の暦の今日・今月）。"""
    if not jobs_permitted():
        return "jobs_not_permitted"
    m = monthly_limit()
    if m <= 0:
        return "monthly_limit_unset"
    n = Q.launches("hf_jobs", now=now, db_path=db_path)
    if n["today"] >= JOBS_PER_DAY:
        return "daily_limit"
    if n["month"] >= m:
        return "monthly_limit"
    return None


def jobs_status(db_path="pox.db", now=None):
    """運営の口と起動のログに出す数（起動の数・上限・許可の有無）。"""
    n = Q.launches("hf_jobs", now=now, db_path=db_path)
    return {"permitted": jobs_permitted(), "today": n["today"], "per_day": JOBS_PER_DAY, "month": n["month"],
            "per_month": monthly_limit(), "gate": launch_gate(db_path, now) or "ok"}


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
        if cfg["prompts"]["label_sha256"] != PR.LABEL_SHA256:
            raise ValueError("設定のラベルの段のプロンプトの sha256 が docs/prompts/ と違う")
        PR.template(PR.LABEL_FILE, PR.LABEL_SHA256)
        PR.template(PR.select_file(cfg["prompts"]["select_sha256"]), cfg["prompts"]["select_sha256"])
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"[FATAL] {BACKEND_ENV}={b} ですが、説明の生成の設定が読めません（{type(e).__name__}: {e}）。")
    need = {"hf_jobs": (HF_TOKEN_ENV,), "local_http": (ENDPOINT_ENV, ENDPOINT_KEY_ENV)}[b]
    missing = [n for n in need if not (os.environ.get(n) or "").strip()]
    if missing:
        raise SystemExit(f"[FATAL] {BACKEND_ENV}={b} ですが {', '.join(missing)} が未設定です（Render の環境変数）。")
    return {"backend": b, "version": version_key(cfg)[:20], "cells": cfg["pipeline"]["cells"],
            "jobs": "許可あり" if jobs_permitted() else "許可なし（起動しない）",
            "jobs_per_day": JOBS_PER_DAY, "jobs_per_month": monthly_limit(),
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
    model = m["repo"] if RT.is_vllm(cfg) else f"{m['repo']}/{m['file']}"
    return {"model": model, "revision": m["revision"], "decoding": RT.decoding(cfg)}


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
                           clock=time.time, cells=RT.cells(cfg), label_sha=cfg["prompts"]["label_sha256"],
                           select_sha=cfg["prompts"]["select_sha256"], bound_for_no_purpose=RT.bound_for_no_purpose(cfg),
                           strip_label_words=RT.strip_label_words(cfg))
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
def allowed(a, b, db_path="pox.db"):
    """生成してよい組か（許可リスト POX_EXPLANATION_ALLOWLIST の組だけ。表示のフラグと同じ許可リスト。指示書84 §1-2）。"""
    return bool(a and b and a != b and frozenset((a, b)) in S.allowlist(db_path))


def enqueue_pair(a, b, db_path="pox.db"):
    """組を 1 つ積む。許可リストに無い組は積まない（None）。戻り値は Q.enqueue と同じ（queued・again・same）。"""
    if not allowed(a, b, db_path):
        return None
    return Q.enqueue(*sorted((a, b)), db_path=db_path)


def enqueue_allowlist(db_path="pox.db"):
    """許可リストの組をぜんぶ積む（運営の口の enqueue）。積んだ組の数。"""
    return sum(1 for pair in S.allowlist(db_path) if enqueue_pair(*sorted(pair), db_path=db_path))


def needs_making(a, b, ctx, db_path="pox.db"):
    """作る必要があるか：二人とも入力があり、いまの入力・いまの版のカードが二つの向きの両方には保存されていないとき True。
    入力の無い人がいる組は False（作れない。ジョブを起こさない）。"""
    if not ctx.inputs_of(a) or not ctx.inputs_of(b):
        return False
    cfg = runtime()
    return not (stored_meta(a, b, ctx, cfg, db_path) and stored_meta(b, a, ctx, cfg, db_path))


def enqueue_for(subject, db_path="pox.db", ctx=None):
    """登録・更新のきっかけ：その人が入っている許可リストの組を積む。推論先が無ければ何もしない。積んだ組の数を返す。
    同じ組は 1 行にまとめる（二人とも登録し直しても、作るのは 1 回。作っている途中なら、終わった後に 1 回だけ作り直す）。
    ctx を渡せば、いまの入力・いまの版のカードがもうある組と、入力の無い人がいる組は積まない（指示書84 §1-5）。"""
    if not backend():
        return 0
    n = 0
    for pair in S.allowlist(db_path):
        if subject in pair:
            a, b = sorted(pair)
            if ctx is not None and not needs_making(a, b, ctx, db_path):
                continue
            if enqueue_pair(a, b, db_path=db_path):
                n += 1
    return n


def _version_record(cfg, b, gpu, extra=None):
    from explain_pipeline import runtime as RT
    return RT.version_record(cfg, b, gpu=gpu, code_commit=os.environ.get("RENDER_GIT_COMMIT"), extra=extra)


def _items(verify, db_path):
    """作る組。予定の組のうち許可リストの組だけ（許可リストから外れた組の行は skipped・not_allowlisted にして作らない）。"""
    if not verify:
        out = []
        for it in Q.pending(db_path):
            if allowed(it["a"], it["b"], db_path):
                out.append(it)
            else:
                Q.skip(it["pair_key"], "not_allowlisted", db_path=db_path)
        return out
    return [{"pair_key": Q.pair_key(*sorted(p)), "a": sorted(p)[0], "b": sorted(p)[1]} for p in sorted(S.allowlist(db_path), key=sorted)]


def _run_items(items, ctx, ep, cfg, b, gpu, extra, verify, db_path):
    made, results = 0, []
    for it in items:
        k = it["pair_key"]
        if not allowed(it["a"], it["b"], db_path):                       # 念のため（借りた GPU に送るのは許可リストの組だけ）
            continue
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
    （保存しない。PC に移したときの確かめ。§4-1 の 7）。戻り値は数と番号だけ。同時に 2 つは動かさない。
    作るのは許可リストの組で、二人とも入力のある組だけ。hf_jobs は launch_gate（許可・1 日 1 回・1 か月の上限）を通ったときだけ起動する。"""
    if not _LOCK.acquire(blocking=False):
        return {"busy": True}
    try:
        b = backend_name or backend()
        if not b:
            return {"backend": "off"}
        Q.reset_stale(db_path=db_path)
        items = _items(verify, db_path)
        keep = []                                       # 入力の無い人がいる組は作れない：ジョブを起こす前に外す
        for it in items:
            if ctx.inputs_of(it["a"]) and ctx.inputs_of(it["b"]):
                keep.append(it)
            elif not verify:
                Q.skip(it["pair_key"], "inputs", db_path=db_path)
        items = keep
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
                from explain_pipeline import runtime as RT
                if RT.is_vllm(cfg) and EP.engine_version(ep) != str(cfg["engine"]["version"]):
                    rec.update(made=0, reason="engine_mismatch")           # 版のエンジン（vLLM の版）と違う推論先では作らない
                    return rec
                made, results = _run_items(items, ctx, ep, cfg, b, os.environ.get(GPU_ENV) or None, None, verify, db_path)
            else:
                gate = launch_gate(db_path)                                 # 費用のかかるジョブは、KAORU の許可と上限の中でだけ
                if gate:
                    rec.update(made=0, reason=gate, jobs=jobs_status(db_path))
                    return rec
                lid = Q.record_launch(b, batch_id=bid, db_path=db_path)     # 起動する前に数える（途中で止まっても数える）
                sess = (session_factory or _hf_session)(cfg)
                try:
                    with sess as ep:
                        gpu = FLAVOR_GPU.get((cfg.get("hf_jobs") or {}).get("flavor"))
                        made, results = _run_items(items, ctx, ep, cfg, b, gpu, {"job_id": sess.record.get("job_id")}, verify, db_path)
                finally:
                    rec["job"] = {k: v for k, v in sess.record.items() if k in JOB_RECORD}
                    Q.set_launch(lid, job_id=sess.record.get("job_id"), record=rec["job"], db_path=db_path)
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


JOB_RECORD = ("job_id", "flavor", "stage", "t_running", "startup_seconds", "startup_after_running", "seconds", "cost_usd_est",
              "stopped", "cancel_failed")


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


# ── 保存した出力を入れる（指示書81 追補3）──────────────────────────────────────────────────
REPLAY_SOURCE = "saved_outputs"


def _sha(t):
    return hashlib.sha256(str(t).encode("utf-8")).hexdigest()


def _line_hashes(t):
    return [_sha(x)[:16] for x in str(t).split("\n")]


def drawn(a, b, ctx, cfg, label_outputs=None):
    """いまの二人の入力から、版の設定で描いたもの（数・番号・ハッシュだけ）。label_outputs（ラベルの段の出力）があれば、
    選ぶ段のプロンプトも描く。返り値: (描いたもの, 内部の材料)。"""
    from explain_pipeline import atoms as AT
    from explain_pipeline import checks as CK
    from explain_pipeline import fields as FL
    from explain_pipeline import prompts as PR
    from explain_pipeline import runtime as RT
    pa, pb = ctx.inputs_of(a), ctx.inputs_of(b)
    if not pa or not pb:
        return None, None
    F, META = FL.pair_fields(pa, pb)
    atoms = AT.build_atoms(F, META)
    lp, cands, _ = PR.label_prompts(atoms, RT.cells(cfg))
    d = {"inputs_sha256": {"A": _sha(_canon(pa)), "B": _sha(_canon(pb))},
         "atoms": [x["id"] for x in atoms],
         "cands": [[c["pair"], c["dir"], c["cell"], c["left"], c["right"]] for c in sorted(cands.values(), key=lambda c: c["pair"])],
         "label_prompts": {k: {"sha256": _sha(v), "chars": len(v), "lines": _line_hashes(v)} for k, v in lp.items()}}
    mat = {"F": F, "META": META, "atoms": atoms, "cands": cands}
    if label_outputs is not None:
        labels = CK.check_labels(label_outputs, cands, {x["id"]: x for x in atoms})
        ssha = cfg["prompts"]["select_sha256"]
        sp, _, _ = PR.select_prompts(atoms, labels, F, META, (), file=PR.select_file(ssha), sha=ssha)
        d["select_prompts"] = {k: {"sha256": _sha(v), "chars": len(v), "lines": _line_hashes(v)} for k, v in sp.items()}
    return d, mat


def order_by_record(a, b, ctx, expected):
    """記録の一人目（A）・二人目（B）に合う順。入力のハッシュが記録と合う向きがあればその順、無ければ (a, b) のまま。"""
    want = (expected or {}).get("inputs_sha256") or {}
    for x, y in ((a, b), (b, a)):
        px, py = ctx.inputs_of(x), ctx.inputs_of(y)
        if px and py and _sha(_canon(px)) == want.get("A") and _sha(_canon(py)) == want.get("B"):
            return x, y
    return a, b


def compare_drawn(got, want):
    """描いたものと v06 の記録を比べる。違う位置と種類だけを返す（文は返さない）。"""
    diffs = []
    for w in ("A", "B"):
        if got["inputs_sha256"][w] != want["inputs_sha256"][w]:
            diffs.append({"kind": "入力", "who": w})
    if got["atoms"] != want["atoms"]:
        ga, wa = got["atoms"], want["atoms"]
        diffs.append({"kind": "部品", "count": [len(wa), len(ga)],
                      "first_index": next((i for i in range(min(len(ga), len(wa))) if ga[i] != wa[i]), min(len(ga), len(wa)))})
    gc, wc = {c[0]: c for c in got["cands"]}, {c[0]: c for c in want["cands"]}
    if gc != wc:
        counts = lambda cs: {f"{d}.{cell}": sum(1 for c in cs.values() if c[1] == d and c[2] == cell)  # noqa: E731
                             for d in ("N_by_O", "O_by_N") for cell in "ABC"}
        diffs.append({"kind": "候補", "count_want": counts(wc), "count_got": counts(gc),
                      "ids": sorted(k for k in set(gc) | set(wc) if gc.get(k) != wc.get(k))[:50]})
    for stage in ("label_prompts", "select_prompts"):
        for pid, w in (want.get(stage) or {}).items():
            g = (got.get(stage) or {}).get(pid)
            if g is None:
                diffs.append({"kind": stage, "id": pid, "why": "描けない"})
            elif g["sha256"] != w["sha256"]:
                gl, wl = g["lines"], w["lines"]
                diffs.append({"kind": stage, "id": pid, "chars": [w["chars"], g["chars"]], "lines": [len(wl), len(gl)],
                              "diff_lines": [i + 1 for i in range(max(len(gl), len(wl)))
                                             if i >= len(gl) or i >= len(wl) or gl[i] != wl[i]][:50]})
    return diffs


class _Replay:
    """保存した出力を、流れの順（ラベル N_by_O・O_by_N → 選ぶ A_need・B_need）に返す推論先。"""

    def __init__(self, outputs):
        self.seq = [outputs["label"]["N_by_O"], outputs["label"]["O_by_N"], outputs["select"]["A_need"], outputs["select"]["B_need"]]
        self.i = 0

    def complete(self, prompt, max_tokens):
        t = time.time()
        text = self.seq[self.i]
        self.i += 1
        return {"text": text, "reasoning": None, "finish_reason": "stop", "usage": None, "timings": None,
                "t_send": t, "t_first": t, "t_first_content": t, "t_end": t}


def replay(a, b, ctx, expected, outputs, source_sha256=None, store_it=False, db_path="pox.db", cfg=None):
    """v06 の記録（expected）と、いまの入力から描いたものを比べ、同じなら（store_it のとき）保存した出力からカードを作って入れる。
    a が記録の一人目（A）。返り値は数・番号・種類だけ。"""
    from explain_pipeline import runner as RN
    from explain_pipeline import runtime as RT
    cfg = cfg or runtime()
    label_out = {k: outputs["label"][k] for k in ("N_by_O", "O_by_N")}
    got, _ = drawn(a, b, ctx, cfg, label_out)
    if got is None:
        return {"same": False, "diffs": [{"kind": "入力", "why": "読めない"}], "stored": False}
    diffs = compare_drawn(got, expected)
    out = {"version": RT.version_id(cfg), "same": not diffs, "diffs": diffs, "stored": False,
           "counts": {"atoms": len(got["atoms"]), "cands": len(got["cands"]),
                      "prompts": {k: len(got.get(k) or {}) for k in ("label_prompts", "select_prompts")}}}
    if diffs or not store_it:
        return out
    pa, pb = ctx.inputs_of(a), ctx.inputs_of(b)
    na, nb = ctx.name_of(a) or "", ctx.name_of(b) or ""
    spec = {"id": Q.pair_key(a, b), "a": pa, "b": pb, "names": [n for n in (na, nb) if n],
            "people": {"A": {"name": na, "tag": tag(pa)}, "B": {"name": nb, "tag": tag(pb)}}, "generation": generation(cfg)}
    ep = _Replay(outputs)
    res_out, rec = RN.run_pair(spec, ep, max_tokens=int(cfg["generation"]["max_tokens"]), cells=RT.cells(cfg),
                               label_sha=cfg["prompts"]["label_sha256"], select_sha=cfg["prompts"]["select_sha256"],
                               bound_for_no_purpose=RT.bound_for_no_purpose(cfg),
                           strip_label_words=RT.strip_label_words(cfg))
    if ep.i != 4:
        out.update(same=False, diffs=[{"kind": "出力の数", "used": ep.i}])
        return out
    rec.pop("pair", None)
    res = {"status": "done", "cards": {a: res_out["cards"]["A"], b: res_out["cards"]["B"]}, "record": rec,
           "people": {a: pa, b: pb}, "order": (a, b)}
    store(res, cfg, _version_record(cfg, REPLAY_SOURCE, None, {"source": REPLAY_SOURCE, "source_sha256": dict(source_sha256 or {})}),
          db_path=db_path)
    out.update(stored=True, version_key=version_key(cfg)[:20], statuses={w: statuses(c) for w, c in (("A", res["cards"][a]), ("B", res["cards"][b]))},
               form_fixes=rec["counts"].get("form_fixes"))
    return out
