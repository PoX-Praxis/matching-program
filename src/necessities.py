#!/usr/bin/env python3
"""
PoX 必要像の 1:N 化（指示書17 §7）。

必要像は主体にも意志形成にも従属しない独立レコード（(C)案）。owner_ref は
subject_id でも intent_id でもよい。derived_necessity の 1:1 前提を崩し、
探索者側（必要像）と候補側（主体レコード）を役割の境界で分ける。

本モジュールが受け持つのは **追記専用で確定させるべき不変条件**（§9 で最も訂正が
高くつく部分）:
  - necessity.published イベント（owner ごとの単調カウンタ n・prev_necessity 鎖）
  - content_hash = H(necessity_text ‖ 数値素材 ‖ commit(evidence_span, salt))（§7-2）
  - evidence_span はソルト付きコミットメントを内容ハッシュの内側に束ねる。平文と salt
    は削除可能な別テーブル（salt を消せば開けなくなる＝削除の意味論）
  - generator はモデルファミリへ丸める（フィンガープリンティング対策・§7-3）
  - 手書き（self_declared）の必要像は gate_u を 0.6 以上にクランプ（§1-5・§7-4）
  - liveness は導出（§7-6）。necessity.retired は自主取り下げのみ書く

ベクトル化（will_vec / necessity_vec）と照合の query 単位切替（§7-5）は、本モジュールの
外＝ベクトル化配線・照合切替の後続段で行う（モデル/prefix は経路によらず同一・§2-3）。
"""
import json
import uuid
import secrets
from datetime import datetime, timezone

from db_connect import get_connection, is_postgres
from canon import canonicalize, sha256_hex
import ledger_events as le

_NUM_KEYS = ("gate_s", "gate_u", "p_sharpness", "alpha", "beta")
SELF_DECLARED_MIN_GATE_U = 0.6   # §1-5・§7-4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS necessities ("
            "necessity_id TEXT PRIMARY KEY, owner_ref TEXT NOT NULL, owner_kind TEXT NOT NULL, "
            "n INTEGER NOT NULL, will_text TEXT NOT NULL, will_vec TEXT, "
            "necessity_text TEXT NOT NULL, necessity_vec TEXT, "
            "gate_s REAL, gate_u REAL, p_sharpness REAL, alpha REAL, beta REAL, "
            "evidence_commit TEXT, content_hash TEXT NOT NULL, origin TEXT NOT NULL, "
            "generator TEXT, prev_necessity TEXT, created_at TEXT NOT NULL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS necessity_evidence ("
            "necessity_id TEXT PRIMARY KEY, evidence_span TEXT, salt TEXT)"
        )
        con.commit()
    return con


# ── 較正・正規化 ────────────────────────────────────────────────────────────

def normalize_generator(name: str) -> str:
    """モデル指紋をファミリ単位へ丸める（§7-3）。'Claude Opus 4.8' → 'claude-opus'。"""
    if not name:
        return ""
    s = str(name).lower()
    families = [
        ("claude-opus", ("opus",)),
        ("claude-sonnet", ("sonnet",)),
        ("claude-haiku", ("haiku",)),
        ("claude", ("claude", "anthropic")),
        ("gpt", ("gpt", "openai", "o1", "o3")),
        ("gemini", ("gemini", "bard")),
        ("qwen", ("qwen",)),
        ("nomic", ("nomic",)),
    ]
    for fam, needles in families:
        if any(k in s for k in needles):
            return fam
    return "other"


def clamp_gate_u(origin: str, gate_u):
    """手書き（self_declared）の必要像は gate_u を 0.6 以上にクランプ（§1-5・§7-4）。

    差分からの導出を経ていない必要像は確信度が原理的に低い。制裁ではなく較正。
    """
    if origin == "self_declared":
        if gate_u is None:
            return SELF_DECLARED_MIN_GATE_U
        return max(float(gate_u), SELF_DECLARED_MIN_GATE_U)
    return gate_u


# ── 内容ハッシュ・コミットメント ────────────────────────────────────────────

def make_salt() -> str:
    return secrets.token_hex(16)


def evidence_commitment(evidence_span: str, salt: str) -> str:
    """ソルト付きコミットメント。salt を失うと開けない（§7-2）。"""
    return sha256_hex((salt + "\x1f" + (evidence_span or "")).encode("utf-8"))


def compute_content_hash(necessity_text: str, numbers: dict, ev_commit: str) -> str:
    """content_hash = H(necessity_text ‖ 数値素材 ‖ commit(evidence_span, salt))（§7-2）。

    数値素材は平文でハッシュに含める（含めないと第三者が再計算できず検証が成立しない）。
    evidence_span 本文は入れず、その **コミットメント** を束ねる（束縛は切らさない）。
    """
    nums = {k: numbers.get(k) for k in _NUM_KEYS}
    return sha256_hex(canonicalize({
        "necessity_text": necessity_text or "",
        "numbers": nums,
        "evidence_commit": ev_commit or "",
    }))


# ── 発行（necessities 行 ＋ necessity.published イベント）────────────────────

def _latest_for_owner(con, owner_ref: str):
    return con.execute(
        "SELECT necessity_id, n FROM necessities WHERE owner_ref=%s ORDER BY n DESC LIMIT 1",
        (owner_ref,),
    ).fetchone()


def publish_necessity(owner_ref: str, owner_kind: str, necessity: dict, *,
                      origin: str = "generated", generator: str = "",
                      actor: str = None, skip_if_unchanged: bool = True,
                      db_path: str = "pox.db") -> dict:
    """必要像を1件発行する。necessities 行を書き、necessity.published を台帳へ追記する。

    - owner_kind: 'subject' | 'intent'
    - origin: 'generated' | 'self_declared'（self_declared は gate_u をクランプ）
    - n は owner ごとの単調カウンタ、prev_necessity は同 owner の直前 necessity。
    - skip_if_unchanged: 直前と content_hash が同一なら行も台帳も足さない（churn 防止。
      編集の再ベクトル化で必要像本文が変わらないケースを弾く）。
    戻り値: {"necessity_id", "n", "content_hash", "prev_necessity", "skipped"}。
    """
    if owner_kind not in ("subject", "intent"):
        raise ValueError("owner_kind は 'subject' | 'intent'")
    if origin not in ("generated", "self_declared"):
        raise ValueError("origin は 'generated' | 'self_declared'")

    necessity_id = f"nec_{uuid.uuid4().hex[:12]}"
    gate_u = clamp_gate_u(origin, necessity.get("gate_u"))
    numbers = {k: (gate_u if k == "gate_u" else necessity.get(k)) for k in _NUM_KEYS}
    will_text = necessity.get("will_text") or necessity.get("意志") or ""
    necessity_text = necessity.get("necessity_text") or ""
    evidence_span = necessity.get("evidence_span") or ""
    gen = normalize_generator(generator or necessity.get("generator_name") or "")

    salt = make_salt()
    ev_commit = evidence_commitment(evidence_span, salt)
    content_hash = compute_content_hash(necessity_text, numbers, ev_commit)

    with _connect(db_path) as con:
        latest = con.execute(
            "SELECT necessity_id, n, content_hash FROM necessities WHERE owner_ref=%s "
            "ORDER BY n DESC LIMIT 1", (owner_ref,),
        ).fetchone()
        # churn 判定: salt はレコード毎に乱数のため content_hash 直比較はできない。
        # 「直前レコードの salt」で新内容のハッシュを再計算し、一致＝同一内容として弾く。
        if skip_if_unchanged and latest:
            prev_salt = con.execute(
                "SELECT salt FROM necessity_evidence WHERE necessity_id=%s", (latest[0],)
            ).fetchone()
            if prev_salt and prev_salt[0] is not None:
                same = compute_content_hash(
                    necessity_text, numbers,
                    evidence_commitment(evidence_span, prev_salt[0])) == latest[2]
                if same:
                    return {"necessity_id": latest[0], "n": latest[1],
                            "content_hash": latest[2], "prev_necessity": None,
                            "skipped": True}
        prev_id = latest[0] if latest else None
        n = (latest[1] + 1) if latest else 1
        con.execute(
            "INSERT INTO necessities (necessity_id, owner_ref, owner_kind, n, will_text, "
            "will_vec, necessity_text, necessity_vec, gate_s, gate_u, p_sharpness, alpha, beta, "
            "evidence_commit, content_hash, origin, generator, prev_necessity, created_at) "
            "VALUES (%s,%s,%s,%s,%s,NULL,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (necessity_id, owner_ref, owner_kind, n, will_text, necessity_text,
             numbers["gate_s"], numbers["gate_u"], numbers["p_sharpness"],
             numbers["alpha"], numbers["beta"], ev_commit, content_hash,
             origin, gen, prev_id, _now()),
        )
        # 平文・salt は削除可能な別テーブル（§7-2）。
        con.execute(
            "INSERT INTO necessity_evidence (necessity_id, evidence_span, salt) VALUES (%s,%s,%s)",
            (necessity_id, evidence_span, salt),
        )

    # 台帳へ（本文は載せない・content_hash のみ・§4-2）。
    le.append_event(actor or owner_ref, "necessity.published", {
        "necessity_id": necessity_id, "owner_ref": owner_ref, "owner_kind": owner_kind,
        "n": n, "content_hash": content_hash, "prev_necessity": prev_id,
        "origin": origin, "generator": gen,
    }, db_path=db_path)

    return {"necessity_id": necessity_id, "n": n,
            "content_hash": content_hash, "prev_necessity": prev_id, "skipped": False}


def retire_necessity(necessity_id: str, reason: str = "", *, actor: str = None,
                     db_path: str = "pox.db") -> dict:
    """自主取り下げ（§7-6）。necessity.retired を書くのはこの経路のみ。

    意志形成の完了に伴う失効は導出（is_live）で足りるため書かない。
    """
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT owner_ref FROM necessities WHERE necessity_id=%s", (necessity_id,)
        ).fetchone()
    if not row:
        return {"necessity_id": necessity_id, "retired": False}
    le.append_event(actor or row[0], "necessity.retired",
                    {"necessity_id": necessity_id, "reason": reason}, db_path=db_path)
    return {"necessity_id": necessity_id, "retired": True}


# ── 参照・liveness ──────────────────────────────────────────────────────────

def _row_to_dict(r) -> dict:
    cols = ("necessity_id", "owner_ref", "owner_kind", "n", "will_text", "necessity_text",
            "gate_s", "gate_u", "p_sharpness", "alpha", "beta", "evidence_commit",
            "content_hash", "origin", "generator", "prev_necessity", "created_at")
    return dict(zip(cols, r))


def latest_published_event_hash(owner_ref: str, db_path: str = "pox.db"):
    """owner の最新 necessity.published の **event_hash**（無ければ None）。接続の根拠解決用（§1-2）。"""
    last = None
    for e in le.get_events(type_="necessity.published", db_path=db_path):
        if e["payload"].get("owner_ref") == owner_ref:
            last = e["event_hash"]
    return last


def get_necessities(owner_ref: str, db_path: str = "pox.db") -> list[dict]:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT necessity_id, owner_ref, owner_kind, n, will_text, necessity_text, "
            "gate_s, gate_u, p_sharpness, alpha, beta, evidence_commit, content_hash, "
            "origin, generator, prev_necessity, created_at FROM necessities "
            "WHERE owner_ref=%s ORDER BY n ASC", (owner_ref,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _retired_ids(db_path: str) -> set:
    return {e["payload"].get("necessity_id")
            for e in le.get_events(type_="necessity.retired", db_path=db_path)}


def is_live(necessity_id: str, db_path: str = "pox.db") -> bool:
    """必要像が生きているか（§7-6）。

    - 自主取り下げ（necessity.retired）されていない
    - prev_necessity 鎖で後続に置換されていない（＝同 owner の後続がこれを指していない）
    - （owner が intent の失効は intent.completed/cancelled 由来だが、それらは未実装のため
      本段では取り下げと置換のみで判定する。§9 で報告）
    """
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT owner_ref FROM necessities WHERE necessity_id=%s", (necessity_id,)
        ).fetchone()
        if not row:
            return False
        superseded = con.execute(
            "SELECT 1 FROM necessities WHERE prev_necessity=%s LIMIT 1", (necessity_id,)
        ).fetchone()
    if superseded:
        return False
    if necessity_id in _retired_ids(db_path):
        return False
    return True


def get_live_necessities(owner_ref: str, db_path: str = "pox.db") -> list[dict]:
    return [n for n in get_necessities(owner_ref, db_path=db_path)
            if is_live(n["necessity_id"], db_path=db_path)]


def open_evidence(necessity_id: str, db_path: str = "pox.db"):
    """本人用に evidence_span 平文を返す（salt 削除後は None）。"""
    with _connect(db_path) as con:
        r = con.execute(
            "SELECT evidence_span, salt FROM necessity_evidence WHERE necessity_id=%s",
            (necessity_id,),
        ).fetchone()
    if not r or r[1] is None:
        return None
    return r[0]


def delete_evidence(necessity_id: str, db_path: str = "pox.db") -> bool:
    """evidence_span 平文と salt を削除（コミットメントは開けなくなる・§7-2）。"""
    with _connect(db_path) as con:
        con.execute("DELETE FROM necessity_evidence WHERE necessity_id=%s", (necessity_id,))
    return True


def get_necessity(necessity_id: str, db_path: str = "pox.db") -> dict | None:
    with _connect(db_path) as con:
        r = con.execute(
            "SELECT necessity_id, owner_ref, owner_kind, n, will_text, necessity_text, "
            "gate_s, gate_u, p_sharpness, alpha, beta, evidence_commit, content_hash, "
            "origin, generator, prev_necessity, created_at FROM necessities "
            "WHERE necessity_id=%s", (necessity_id,),
        ).fetchone()
    return _row_to_dict(r) if r else None


# ── ベクトル化（§7-4）───────────────────────────────────────────────────────
# モデルと prefix は入力経路によらず同一（§2-3）。build_vectors と同じ embed() を通す。
# 必要像レコードが担ぐのは query 側の2本: will_symmetric（意志・対称の複製）と
# necessity_query（必要像）。will_passage / state_passage は主体レコード側（1:1）。

def _default_embed(text, role):
    """embedding_service.embed の full ベクトルを返す（build_vectors と同一の redact→embed）。"""
    from embedding_service import embed as _embed
    from pii_redaction import redact_text
    full, _short = _embed(redact_text(text or ""), role)
    return full


def vectorize_necessity(necessity_id: str, *, embed_fn=None, db_path: str = "pox.db") -> dict | None:
    """必要像レコードの will_symmetric / necessity_query を生成して保存する（§7-4）。

    embed_fn(text, role)->list を注入するとテストで実サービスを使わない。
    既定は embedding_service.embed（BACKEND に従う。本番は nomic）。
    照合の骨格・prefix・MRL には触れない（同一 embed を呼ぶだけ）。
    """
    rec = get_necessity(necessity_id, db_path=db_path)
    if rec is None:
        return None
    ef = embed_fn or _default_embed
    will_sym = ef(rec["will_text"], "symmetric")     # a チャネル用（対称の複製）
    nq = ef(rec["necessity_text"], "query")          # b チャネル用（必要像）
    with _connect(db_path) as con:
        con.execute(
            "UPDATE necessities SET will_vec=%s, necessity_vec=%s WHERE necessity_id=%s",
            (json.dumps(will_sym), json.dumps(nq), necessity_id),
        )
    return {"necessity_id": necessity_id, "dim": len(nq)}


def query_vectors(necessity_id: str, db_path: str = "pox.db") -> dict | None:
    """照合の query 側2本を返す（未ベクトル化なら None）。"""
    with _connect(db_path) as con:
        r = con.execute(
            "SELECT will_vec, necessity_vec FROM necessities WHERE necessity_id=%s",
            (necessity_id,),
        ).fetchone()
    if not r or r[0] is None or r[1] is None:
        return None
    return {"will_symmetric": json.loads(r[0]), "necessity_query": json.loads(r[1])}
