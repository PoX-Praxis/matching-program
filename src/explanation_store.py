"""接続の説明 — 段階1 の本番の側（指示書68 §2〜§6）。フラグ・許可リスト・鍵付きのハッシュ・固定のデータ・保存。

- フラグ `POX_EXPLANATION`: off（既定）／allowlist／on。知らない値は off（閉じる側に倒す）
- 許可リスト `POX_EXPLANATION_ALLOWLIST`: 「a+b,c+d」。a・b は subject_id か @ハンドル。どちら向きでも、その組の二人の間だけ
  空・形の違う組・解決できないハンドルは、その組を誰にも出さない（閉じる側に倒す）
- 鍵 `POX_EXPLANATION_DIGEST_KEY`（32 バイト以上）: render_digest＝HMAC-SHA256(鍵, その人が見た表示の HTML)
  記録ごとに鍵の識別子（鍵で固定の文字列の HMAC を取った先頭 8 桁）を添える。古い記録は `POX_EXPLANATION_DIGEST_KEY_PREV` で確かめる
  鍵はコードにもログにも出さない。開発（POX_DEBUG=1）だけ固定の開発用の鍵
- 説明の向きのレコード（誰の目的 × 誰の文）は本体の DB の表 explanation_directions に置く（§1-2・KAORU 2026-11-10）
  段階1 は運営用の取り込みのルート（POST /ops/explanations/import）で入れる。段階2 で生成した説明も同じ表に入れる
  取り込みでは、スキーマと機械の確かめに落ちた向き・人が解決できない向きを保存しない。同じ向き・同じ as-of は上書き
  画面では、向き 2 つから見る人 × 相手のカードを組み立てる。本人たちの文はリポジトリに入れない
- 保存（本体の DB。台帳には書かない）: 申請に添えた表示・承認の時に二人が見た表示のハッシュ・補足
  取り下げ・見送りで、保存した表示を消す。見送りの補足は、申請した人に届けた後に消す

数値（g・gate・スコア）・生テキスト・根拠は扱わない。
"""
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone

from db_connect import get_connection, is_postgres

import explanation as X

MODES = ("off", "allowlist", "on")
KEY_ENV, KEY_PREV_ENV = "POX_EXPLANATION_DIGEST_KEY", "POX_EXPLANATION_DIGEST_KEY_PREV"
KEY_MIN_BYTES = 32
_DEV_KEY = b"pox-dev-only-explanation-digest-key-0000"        # 開発（POX_DEBUG=1）だけ。本番では使わない
_KEY_ID_LABEL = b"pox-explanation-key-id"
FILE_FORMAT = "pox-explanation-directions-v1"
SUPPLEMENT_MAX = 400
SUPPLEMENT_KINDS = ("self", "approve", "decline")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _debug():
    return os.environ.get("POX_DEBUG", "0") == "1"


# ── フラグ ───────────────────────────────────────────────────────────────────────
def mode():
    v = (os.environ.get("POX_EXPLANATION") or "off").strip().lower()
    return v if v in MODES else "off"


# ── 鍵付きのハッシュ（§4）───────────────────────────────────────────────────────────
class KeyMissing(RuntimeError):
    pass


def _env_key(name):
    raw = (os.environ.get(name) or "").encode("utf-8")
    return raw if len(raw) >= KEY_MIN_BYTES else None


def digest_key():
    k = _env_key(KEY_ENV)
    if k:
        return k
    if _debug():
        return _DEV_KEY
    raise KeyMissing(f"{KEY_ENV} が未設定か短すぎます（{KEY_MIN_BYTES} バイト以上）")


def key_id(key):
    """鍵の識別子: 鍵で固定の文字列の HMAC を取った先頭 8 桁（鍵そのものは分からない）。"""
    return hmac.new(key, _KEY_ID_LABEL, hashlib.sha256).hexdigest()[:8]


def render_digest(html_text):
    """(render_digest, digest_key_id)。"""
    k = digest_key()
    return hmac.new(k, html_text.encode("utf-8"), hashlib.sha256).hexdigest(), key_id(k)


def verify_digest(html_text, digest, kid):
    """保存した表示が、記録のハッシュと合うか（いまの鍵か、ひとつ前の鍵で）。"""
    keys = [k for k in (_env_key(KEY_ENV), _env_key(KEY_PREV_ENV)) if k] + ([_DEV_KEY] if _debug() else [])
    for k in keys:
        if key_id(k) == kid:
            want = hmac.new(k, html_text.encode("utf-8"), hashlib.sha256).hexdigest()
            return hmac.compare_digest(want, digest or "")
    return False


def startup_check():
    """起動のときに呼ぶ。allowlist・on で鍵が無ければ、本番は起動しない（§7-9）。ログに出すのは設定の有無だけ。"""
    m = mode()
    has_key = _env_key(KEY_ENV) is not None
    if m in ("allowlist", "on") and not has_key and not _debug():
        raise SystemExit(f"[FATAL] POX_EXPLANATION={m} ですが {KEY_ENV} が未設定か短すぎます（{KEY_MIN_BYTES} バイト以上）。"
                         "Render の環境変数に設定してください（開発時のみ POX_DEBUG=1 で開発用の鍵を使います）。")
    return {"mode": m, "key": "設定あり" if has_key else "なし",
            "key_prev": "設定あり" if _env_key(KEY_PREV_ENV) else "なし"}


# ── 許可リスト（§6）────────────────────────────────────────────────────────────────
def _resolve_subject(token, db_path):
    t = (token or "").strip()
    if not t:
        return None
    if t.startswith("@"):
        import handles
        return handles.subject_for(t, db_path=db_path)
    return t


def allowlist(db_path="pox.db"):
    """許可された組（frozenset の集合）。形の違う組・解決できない組は入れない（閉じる側に倒す）。"""
    out = set()
    for pair in (os.environ.get("POX_EXPLANATION_ALLOWLIST") or "").split(","):
        parts = [p.strip() for p in pair.split("+")]
        if len(parts) != 2 or not all(parts):
            continue
        a, b = (_resolve_subject(p, db_path) for p in parts)
        if a and b and a != b:
            out.add(frozenset((a, b)))
    return out


def enabled_for(a, b, db_path="pox.db"):
    m = mode()
    if m == "off" or not a or not b or a == b:
        return False
    if m == "on":
        return True
    return frozenset((a, b)) in allowlist(db_path)


# ── 説明の向きのレコード（§1-2。本体の DB）と取り込み（§5-2 案 B）──────────────────────────────────
def parse_import(doc):
    """取り込む JSON → ([(need_label, offer_label, direction)], {label: subject}, {label: has_purposes})。
    形は 2 つ受け付ける: 向きのレコード（format=pox-explanation-directions-v1）と、カードの一覧（cards）。"""
    if not isinstance(doc, dict):
        raise ValueError("JSON のオブジェクトではない")
    people = doc.get("people") or {}
    subj = {k: (v or {}).get("subject") for k, v in people.items()}
    hp = {k: bool((v or {}).get("has_purposes", True)) for k, v in people.items()}
    dirs = []
    if doc.get("format") == FILE_FORMAT:
        for d in doc.get("directions") or []:
            dirs.append((d.get("need"), d.get("offer"), {k: d.get(k) for k in X.DIRECTION_FIELDS}))
    for c in doc.get("cards") or []:                  # カードの形（見る人 × 相手）は、向き 2 つに分ける
        try:
            fwd, rev = X.split_card(c["card"])
        except Exception:  # noqa: BLE001 — 分けられないカードは、両方の向きを「スキーマに通らない」にする
            fwd = rev = None
        dirs += [(c.get("viewer"), c.get("target"), fwd), (c.get("target"), c.get("viewer"), rev)]
        if isinstance(c.get("card"), dict):
            hp.setdefault(c.get("target"), bool(c["card"].get("target_has_purposes", True)))
    return dirs, subj, hp


def as_of_digest(direction):
    s = json.dumps(direction.get("as_of") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def check_direction(direction, names=()):
    """向き 1 つにスキーマと機械の確かめを当てる。戻り値: 落ちた理由の種類（通れば None）。文は返さない。"""
    if not isinstance(direction, dict):
        return "スキーマに通らない"
    try:
        card = X.assemble(direction, None, True)
        ok, _why = X.validate(card)
    except Exception:  # noqa: BLE001
        return "スキーマに通らない"
    if not ok:
        return "スキーマに通らない"
    _checked, problems = X.check(card, names=names)
    if problems:
        return "機械の確かめに落ちた"
    return None


def import_doc(doc, db_path="pox.db", names_of=None):
    """取り込み（§5-2 案 B）。通った向きだけを保存する（同じ向き・同じ as-of は上書き）。
    戻り値は件数と、向きごとの通過の可否だけ（文・ラベル・subject は返さない）。names_of(subject) は呼び方（① に名前が無いかの確かめ用）。"""
    raw, subj, hp = parse_import(doc)
    results, saved = [], 0
    seen = set()
    for i, (need, offer, d) in enumerate(raw):
        a = _resolve_subject(subj.get(need) or "", db_path) if need in subj else None
        b = _resolve_subject(subj.get(offer) or "", db_path) if offer in subj else None
        if not a or not b or a == b:
            results.append({"index": i, "ok": False, "why": "人が解決できない"})
            continue
        if (a, b) in seen:
            results.append({"index": i, "ok": True, "why": "同じ向きの重なり（1 つとして保存）"})
            continue
        names = tuple(n for n in ((names_of(a), names_of(b)) if names_of else ()) if n)
        why = check_direction(d, names=names)
        if why:
            results.append({"index": i, "ok": False, "why": why})
            continue
        save_direction(a, b, d, need_has_purposes=hp.get(need, True), source="import", db_path=db_path)
        seen.add((a, b))
        saved += 1
        results.append({"index": i, "ok": True})
    return {"received": len(raw), "saved": saved, "dropped": sum(1 for r in results if not r["ok"]), "directions": results}


def save_direction(need_subject, offer_subject, direction, need_has_purposes=True, source="import", db_path="pox.db"):
    body = json.dumps({k: direction.get(k) for k in X.DIRECTION_FIELDS}, ensure_ascii=False, sort_keys=True)
    now = _now()
    with _connect(db_path) as con:
        con.execute("INSERT INTO explanation_directions (need_subject, offer_subject, as_of_digest, direction_json, "
                    "need_has_purposes, source, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (need_subject, offer_subject, as_of_digest) DO UPDATE SET "
                    "direction_json=EXCLUDED.direction_json, need_has_purposes=EXCLUDED.need_has_purposes, "
                    "source=EXCLUDED.source, updated_at=EXCLUDED.updated_at",
                    (need_subject, offer_subject, as_of_digest(direction), body, 1 if need_has_purposes else 0,
                     source, now, now))


def _latest_direction(con, need_subject, offer_subject):
    r = con.execute("SELECT direction_json, need_has_purposes FROM explanation_directions "
                    "WHERE need_subject=%s AND offer_subject=%s ORDER BY updated_at DESC, created_at DESC",
                    (need_subject, offer_subject)).fetchone()
    return (json.loads(r[0]), bool(r[1])) if r else (None, None)


def status(db_path="pox.db"):
    """起動のログ用: 向きのレコードの数だけ（文は出さない）。"""
    with _connect(db_path) as con:
        return {"directions": con.execute("SELECT COUNT(*) FROM explanation_directions").fetchone()[0]}


def card_for(viewer, target, db_path="pox.db"):
    """見る人 × 相手のカード（フラグ・許可リストを通ったときだけ）。無ければ None。"""
    if not enabled_for(viewer, target, db_path):
        return None
    with _connect(db_path) as con:
        fwd, _ = _latest_direction(con, viewer, target)
        rev, rev_hp = _latest_direction(con, target, viewer)
    if not fwd and not rev:
        return None
    return X.assemble(fwd, rev, True if rev_hp is None else rev_hp)


def partners_of(viewer, db_path="pox.db"):
    """見る人と説明のある相手（どちらかの向きのレコードがある相手）。並びは取り込んだ順（中立）。"""
    if mode() == "off":
        return []
    with _connect(db_path) as con:
        rows = con.execute("SELECT need_subject, offer_subject, MIN(created_at) AS first FROM explanation_directions "
                           "WHERE need_subject=%s OR offer_subject=%s GROUP BY need_subject, offer_subject "
                           "ORDER BY first, need_subject, offer_subject", (viewer, viewer)).fetchall()
    out = []
    for a, b, _first in rows:
        other = b if a == viewer else a
        if other not in out and enabled_for(viewer, other, db_path):
            out.append(other)
    return out


# ── 保存（§2-3・§2-4・§3・§4。本体の DB。台帳には書かない）──────────────────────────────────
_DDL = [
    """CREATE TABLE IF NOT EXISTS explanation_directions (
        need_subject      TEXT NOT NULL,
        offer_subject     TEXT NOT NULL,
        as_of_digest      TEXT NOT NULL,
        direction_json    TEXT NOT NULL,
        need_has_purposes INTEGER NOT NULL DEFAULT 1,
        source            TEXT NOT NULL,
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL,
        PRIMARY KEY (need_subject, offer_subject, as_of_digest)
    )""",
    """CREATE TABLE IF NOT EXISTS explanation_renders (
        id               TEXT PRIMARY KEY,
        from_subject     TEXT NOT NULL,
        to_subject       TEXT NOT NULL,
        symbolic_html    TEXT NOT NULL,
        viewer_html      TEXT NOT NULL,
        card_digest      TEXT NOT NULL,
        viewer_digest    TEXT NOT NULL,
        viewer_key_id    TEXT NOT NULL,
        recipient_digest TEXT,
        recipient_key_id TEXT,
        pair_digest      TEXT,
        status           TEXT NOT NULL,
        created_at       TEXT NOT NULL,
        approved_at      TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS explanation_supplements (
        id           TEXT PRIMARY KEY,
        render_id    TEXT,
        from_subject TEXT NOT NULL,
        to_subject   TEXT NOT NULL,
        kind         TEXT NOT NULL,
        text         TEXT NOT NULL,
        created_at   TEXT NOT NULL
    )""",
]


def _connect(db_path="pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        for ddl in _DDL:
            con.execute(ddl)
        con.commit()
    return con


_RENDER_COLS = ("id", "from_subject", "to_subject", "symbolic_html", "viewer_html", "card_digest", "viewer_digest",
                "viewer_key_id", "recipient_digest", "recipient_key_id", "pair_digest", "status", "created_at", "approved_at")


def _row(r):
    return dict(zip(_RENDER_COLS, r)) if r else None


def clean_supplement(text):
    """補足の文（400 字まで）。長すぎれば ValueError。空なら None。"""
    t = (text or "").strip()
    if len(t) > SUPPLEMENT_MAX:
        raise ValueError(f"補足は {SUPPLEMENT_MAX} 字までです")
    return t or None


def save_offer_render(applicant, recipient, symbolic_html, viewer_html, card_digest, db_path="pox.db"):
    """申請に添えて、申請した人が読んだ表示をそのまま保存する（§2-3）。前の未承認の表示は置き換える。"""
    dg, kid = render_digest(viewer_html)
    rid = f"er_{uuid.uuid4().hex[:16]}"
    with _connect(db_path) as con:
        con.execute("DELETE FROM explanation_renders WHERE from_subject=%s AND to_subject=%s AND status='pending'",
                    (applicant, recipient))
        con.execute("INSERT INTO explanation_renders (id, from_subject, to_subject, symbolic_html, viewer_html, card_digest, "
                    "viewer_digest, viewer_key_id, recipient_digest, recipient_key_id, pair_digest, status, created_at, approved_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, NULL, 'pending', %s, NULL)",
                    (rid, applicant, recipient, symbolic_html, viewer_html, card_digest, dg, kid, _now()))
    return rid


def pending_render(applicant, recipient, db_path="pox.db"):
    with _connect(db_path) as con:
        r = con.execute(f"SELECT {', '.join(_RENDER_COLS)} FROM explanation_renders "
                        "WHERE from_subject=%s AND to_subject=%s AND status='pending' ORDER BY created_at DESC",
                        (applicant, recipient)).fetchone()
    return _row(r)


def approve_render(render_id, recipient_html, supplement=None, db_path="pox.db"):
    """承認の時に、受けた人が見た表示のハッシュを添えて承認済みにする。補足があれば本人の言葉として保存する（§3）。"""
    dg, kid = render_digest(recipient_html)
    with _connect(db_path) as con:
        r = con.execute("SELECT from_subject, to_subject FROM explanation_renders WHERE id=%s", (render_id,)).fetchone()
        if not r:
            return False
        con.execute("UPDATE explanation_renders SET status='approved', recipient_digest=%s, recipient_key_id=%s, "
                    "approved_at=%s WHERE id=%s", (dg, kid, _now(), render_id))
        if supplement:
            con.execute("INSERT INTO explanation_supplements (id, render_id, from_subject, to_subject, kind, text, created_at) "
                        "VALUES (%s, %s, %s, %s, 'approve', %s, %s)",
                        (f"es_{uuid.uuid4().hex[:16]}", render_id, r[1], r[0], supplement, _now()))
    return True


def approved_render(a, b, db_path="pox.db"):
    """二人の間の承認済みの表示（どちら向きの申請でも）と、承認のときの補足。無ければ None。"""
    with _connect(db_path) as con:
        r = con.execute(f"SELECT {', '.join(_RENDER_COLS)} FROM explanation_renders WHERE status='approved' AND "
                        "((from_subject=%s AND to_subject=%s) OR (from_subject=%s AND to_subject=%s)) "
                        "ORDER BY approved_at DESC", (a, b, b, a)).fetchone()
        row = _row(r)
        if not row:
            return None
        sup = con.execute("SELECT from_subject, text, created_at FROM explanation_supplements "
                          "WHERE render_id=%s AND kind='approve' ORDER BY created_at", (row["id"],)).fetchall()
    row["supplements"] = [{"from": s[0], "text": s[1], "at": s[2]} for s in sup]
    return row


def delete_pending(applicant, recipient, db_path="pox.db"):
    """取り下げ・見送りで、申請に添えた表示を消す（§4）。消した数。"""
    with _connect(db_path) as con:
        ids = [r[0] for r in con.execute("SELECT id FROM explanation_renders WHERE from_subject=%s AND to_subject=%s "
                                         "AND status='pending'", (applicant, recipient)).fetchall()]
        for rid in ids:
            con.execute("DELETE FROM explanation_supplements WHERE render_id=%s", (rid,))
            con.execute("DELETE FROM explanation_renders WHERE id=%s", (rid,))
    return len(ids)


def add_supplement(kind, from_subject, to_subject, text, render_id=None, db_path="pox.db"):
    if kind not in SUPPLEMENT_KINDS:
        raise ValueError("kind")
    sid = f"es_{uuid.uuid4().hex[:16]}"
    with _connect(db_path) as con:
        con.execute("INSERT INTO explanation_supplements (id, render_id, from_subject, to_subject, kind, text, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)", (sid, render_id, from_subject, to_subject, kind, text, _now()))
    return sid


def take_decline_notes(applicant, db_path="pox.db"):
    """見送りに添えられた補足を、申請した人に届ける（読んだら消す。§3「届けた後に残さない」）。"""
    with _connect(db_path) as con:
        rows = con.execute("SELECT id, from_subject, text, created_at FROM explanation_supplements "
                           "WHERE to_subject=%s AND kind='decline' ORDER BY created_at", (applicant,)).fetchall()
        for r in rows:
            con.execute("DELETE FROM explanation_supplements WHERE id=%s", (r[0],))
    return [{"from": r[1], "text": r[2], "at": r[3]} for r in rows]


def count_rows(db_path="pox.db"):
    """テストと運用の確かめ用: 表ごとの行の数。"""
    with _connect(db_path) as con:
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("explanation_renders", "explanation_supplements")}


# ── 取り込みのトークン（§5-2 案 B）────────────────────────────────────────────────────
OPS_TOKEN_ENV = "POX_OPS_IMPORT_TOKEN"
OPS_TOKEN_MIN = 32
OPS_TOKEN_HEADER = "X-Ops-Import-Token"
IMPORT_MAX_BYTES = 1_000_000


def ops_token_ok(header_value):
    """トークンが設定され（32 文字以上）、ヘッダと定数時間の比較で一致するか。未設定・短い・不一致は False（ルートは 404）。"""
    tok = os.environ.get(OPS_TOKEN_ENV) or ""
    if len(tok) < OPS_TOKEN_MIN:
        return False
    return hmac.compare_digest((header_value or "").encode("utf-8"), tok.encode("utf-8"))
