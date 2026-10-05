#!/usr/bin/env python3
"""
①v5（目的ごとの必要像・与え像・文単位）の受信・保存（指示書57 段2）。

- 受信の検証規則（文面 2026-10-06 の 10 項目）: validate(doc, narrative)
- 目的の id はサーバーが振る（不変）。①の purpose_id は対応付けのヒントにだけ使う
- 与え像は版ごとに通常DB（offers）。offer_hash を各目的の必要像の c3 ハッシュに含める（ピン留め・案 A）
- 文単位のベクトル（sentence_vectors）: 必要像の文（query）と与え像の文（passage）を個別に埋め込む
- v4 互換: v4 の人は「目的 1 つ・与え像なし」として読む（与え像の代わりに現状で照合する）
"""
import json
import re
import uuid
from datetime import datetime, timezone

from canon import canonicalize, sha256_hex
from db_connect import get_connection, is_postgres

TYPES = ("向かう先", "関わり方", "資源", "関心")
MAX_PURPOSES, MAX_SENTENCES, MAX_REQUIRED = 3, 5, 2
# 求人票の文体（規則 8）
FORBIDDEN = ("年以上", "必須スキル", "ができる方", "を募集")
# 「向かう先」への手段の混入（規則 3）。誤って弾かないよう最小限の語だけを見る。
MEANS_MARKERS = ("そのために", "手段として")
DROP_KEYS = ("p_sharpness", "gamma")              # 規則 7: あれば捨てる


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute("CREATE TABLE IF NOT EXISTS purposes (purpose_id TEXT PRIMARY KEY, "
                    "subject_id TEXT NOT NULL, created_at TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS offers (offer_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, "
                    "n INTEGER NOT NULL, body_json TEXT NOT NULL, offer_hash TEXT NOT NULL, "
                    "created_at TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS sentence_vectors (ref_kind TEXT NOT NULL, ref_id TEXT NOT NULL, "
                    "idx INTEGER NOT NULL, model_tag TEXT NOT NULL, text TEXT NOT NULL, vec TEXT NOT NULL, "
                    "PRIMARY KEY (ref_kind, ref_id, idx, model_tag))")
        con.execute("CREATE TABLE IF NOT EXISTS profile_v5 (subject_id TEXT PRIMARY KEY, doc_json TEXT NOT NULL, "
                    "narrative TEXT, updated_at TEXT NOT NULL)")
        con.commit()
    return con


def is_v5(doc) -> bool:
    return isinstance(doc, dict) and str(doc.get("schema_version") or "") == "v5"


# ── 受信の検証（文面の 10 項目）────────────────────────────────────────────────
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def narrative_of(doc: dict, narrative=None) -> str:
    """本人の語り（生テキスト）。登録画面で別に貼られた語りを優先し、無ければ supporting_material から。"""
    if narrative:
        return str(narrative)
    raw = ((doc or {}).get("supporting_material") or {}).get("生テキスト")
    if isinstance(raw, list):
        return "\n".join(str(x) for x in raw)
    return str(raw or "")


def validate(doc: dict, narrative: str = None):
    """検証規則（文面 §受信側）。通らなければ (False, 理由 1 行)。通れば (True, 正規化した doc)。

    1 schema_version / 2 目的の数 / 3 向かう先と手段（混入）/ 4 必要像の文数・必須の数 / 5 与え像の文数 /
    6 型 / 7 p_sharpness・gamma は捨てる / 8 求人票の文体 / 9 根拠が語りに実在 /
    10 purpose_id はヒント（ここでは触らない。assign_purposes が振り直す）
    """
    if not is_v5(doc):
        return False, "schema_version が v5 ではありません"
    purposes = doc.get("purposes")
    if not isinstance(purposes, list) or not (1 <= len(purposes) <= MAX_PURPOSES):
        return False, f"目的（purposes）は 1〜{MAX_PURPOSES} 件にしてください"
    story = _norm(narrative_of(doc, narrative))
    for i, p in enumerate(purposes, 1):
        if not isinstance(p, dict):
            return False, f"目的 {i} の形が正しくありません"
        dest, means = str(p.get("向かう先") or "").strip(), str(p.get("手段") or "").strip()
        if not dest or not means:
            return False, f"目的 {i}: 「向かう先」と「手段」を別々に書いてください"
        if any(m in dest for m in MEANS_MARKERS):
            return False, f"目的 {i}: 「向かう先」に手段が混ざっています（「手段」に分けてください）"
        needs = p.get("必要像")
        if not isinstance(needs, list) or not (1 <= len(needs) <= MAX_SENTENCES):
            return False, f"目的 {i}: 必要像は 1〜{MAX_SENTENCES} 文にしてください"
        if sum(1 for x in needs if isinstance(x, dict) and x.get("必須") is True) > MAX_REQUIRED:
            return False, f"目的 {i}: 必須は {MAX_REQUIRED} つまでです（それ以外は「歓迎」に）"
        for x in needs:
            if not isinstance(x, dict) or not str(x.get("文") or "").strip():
                return False, f"目的 {i}: 必要像の文が空です"
            if x.get("型") not in TYPES:
                return False, f"目的 {i}: 型は {'／'.join(TYPES)} のいずれかです"
        ev = str(p.get("根拠") or "").strip()
        if ev and _norm(ev) not in story:
            return False, f"目的 {i}: 根拠が本人の語りに見つかりません（引用は原文のまま）"
        for k in DROP_KEYS:
            (p.get("数値") or {}).pop(k, None)
    offers = doc.get("与え像")
    if not isinstance(offers, list) or not (1 <= len(offers) <= MAX_SENTENCES):
        return False, f"与え像は 1〜{MAX_SENTENCES} 文にしてください"
    for x in offers:
        if not isinstance(x, dict) or not str(x.get("文") or "").strip():
            return False, "与え像の文が空です"
        if x.get("型") not in TYPES:
            return False, f"与え像の型は {'／'.join(TYPES)} のいずれかです"
    blob = json.dumps({"p": purposes, "o": offers}, ensure_ascii=False)
    for w in FORBIDDEN:
        if w in blob:
            return False, f"求人票の文体（「{w}」）は使えません"
    return True, doc


# ── v4 の受付に渡す形（profiles_v4 と全文ベクトルの互換）─────────────────────────
def to_flat(doc: dict, narrative: str = None) -> dict:
    """v5 を、既存の v4 受付（profiles_v4・全文ベクトル・スナップショット）に渡すフラットな形にする。

    全文ベクトル（互換）は最初の目的の必要像で作る。目的ごとの照合は文単位ベクトルで行う。
    """
    purposes = doc.get("purposes") or []
    first = purposes[0] if purposes else {}
    state = doc.get("現状") or {}
    sm = dict(doc.get("supporting_material") or {})
    story = narrative_of(doc, narrative)
    if story:
        sm["生テキスト"] = [story]
    nums = first.get("数値") or {}
    return {
        "will_text": "\n".join(str(p.get("向かう先") or "") for p in purposes),
        "state_have": state.get("持っているもの", ""), "state_can_type": state.get("できること_型", ""),
        "state_bound": state.get("縛られているもの", ""), "state_unsorted": state.get("未分類", ""),
        "supporting_raw": sm,
        "necessity_text": "\n".join(str(x.get("文") or "") for x in (first.get("必要像") or [])),
        "gate_s": nums.get("gate_s"), "gate_u": nums.get("gate_u"), "p_sharpness": 0.0,
        "alpha": nums.get("alpha"), "beta": nums.get("beta"),
        "evidence_span": str(first.get("根拠") or ""),
    }


# ── 目的の id（サーバーが振る・不変）──────────────────────────────────────────
def list_purposes(subject_id: str, db_path: str = "pox.db") -> list:
    with _connect(db_path) as con:
        rows = con.execute("SELECT purpose_id FROM purposes WHERE subject_id=%s ORDER BY created_at, purpose_id",
                           (subject_id,)).fetchall()
    return [r[0] for r in rows]


def suggest_mapping(subject_id: str, doc: dict, db_path: str = "pox.db") -> dict:
    """再構造化のときの対応付けの提案（本人が確認する）。①の purpose_id は「p1, p2…」の順のヒント。
    既存の目的を順に当てる。足りない分は "new"。戻り値 {①の purpose_id: 既存 id | "new"}。"""
    existing = [p for p in list_purposes(subject_id, db_path) if is_live_purpose(subject_id, p, db_path)]
    out = {}
    for i, p in enumerate(doc.get("purposes") or []):
        hint = str(p.get("purpose_id") or f"p{i + 1}")
        out[hint] = existing[i] if i < len(existing) else "new"
    return out


def assign_purposes(subject_id: str, doc: dict, mapping: dict = None, db_path: str = "pox.db") -> list:
    """目的にサーバーの不変 id を振る。mapping は本人が確かめた対応 {①の id: 既存 id | "new"}。
    既存 id は本人の目的に限る。同じ既存 id を 2 つの目的に当てたら 2 つ目は新しい目的にする。
    戻り値: [(purpose_id, 目的の doc), ...]（①の順）。"""
    mine = set(list_purposes(subject_id, db_path))
    used, out = set(), []
    with _connect(db_path) as con:
        for i, p in enumerate(doc.get("purposes") or []):
            hint = str(p.get("purpose_id") or f"p{i + 1}")
            want = (mapping or {}).get(hint)
            if want in mine and want not in used:
                pid = want
            else:
                pid = f"pur_{uuid.uuid4().hex[:10]}"
                con.execute("INSERT INTO purposes (purpose_id, subject_id, created_at) VALUES (%s,%s,%s)",
                            (pid, subject_id, _now()))
            used.add(pid)
            out.append((pid, p))
    return out


def is_live_purpose(subject_id: str, purpose_id: str, db_path: str = "pox.db") -> bool:
    import necessities as N
    return any(n.get("purpose_id") == purpose_id for n in live_necessities_v5(subject_id, db_path))


# ── 与え像（版ごと）─────────────────────────────────────────────────────────────
def offer_hash(offers: list) -> str:
    return sha256_hex(canonicalize({"与え像": [{"文": str(x.get("文") or ""), "型": str(x.get("型") or "")}
                                              for x in (offers or [])]}))


def save_offer(subject_id: str, offers: list, db_path: str = "pox.db") -> dict:
    """与え像の版を保存する（同じ内容なら既存を返す）。台帳には書かない（必要像の c3 にピン留めされる）。"""
    h = offer_hash(offers)
    with _connect(db_path) as con:
        last = con.execute("SELECT offer_id, n, offer_hash FROM offers WHERE subject_id=%s ORDER BY n DESC LIMIT 1",
                           (subject_id,)).fetchone()
        if last and last[2] == h:
            return {"offer_id": last[0], "offer_hash": h, "n": last[1], "skipped": True}
        n = (last[1] + 1) if last else 1
        oid = f"off_{uuid.uuid4().hex[:10]}"
        con.execute("INSERT INTO offers (offer_id, subject_id, n, body_json, offer_hash, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (oid, subject_id, n, json.dumps(offers, ensure_ascii=False), h, _now()))
    return {"offer_id": oid, "offer_hash": h, "n": n, "skipped": False}


def latest_offer(subject_id: str, db_path: str = "pox.db"):
    with _connect(db_path) as con:
        r = con.execute("SELECT offer_id, body_json, offer_hash FROM offers WHERE subject_id=%s "
                        "ORDER BY n DESC LIMIT 1", (subject_id,)).fetchone()
    if not r:
        return None
    return {"offer_id": r[0], "sentences": json.loads(r[1]), "offer_hash": r[2]}


def get_offer(offer_id: str, db_path: str = "pox.db"):
    with _connect(db_path) as con:
        r = con.execute("SELECT offer_id, body_json, offer_hash, subject_id FROM offers WHERE offer_id=%s",
                        (offer_id,)).fetchone()
    if not r:
        return None
    return {"offer_id": r[0], "sentences": json.loads(r[1]), "offer_hash": r[2], "subject_id": r[3]}


def save_doc(subject_id: str, doc: dict, narrative: str = None, db_path: str = "pox.db"):
    """v5 の本文（目的の向かう先・手段・関心）を通常DBに保存する。語りは本人のみ・検査用。"""
    with _connect(db_path) as con:
        con.execute("DELETE FROM profile_v5 WHERE subject_id=%s", (subject_id,))
        con.execute("INSERT INTO profile_v5 (subject_id, doc_json, narrative, updated_at) VALUES (%s,%s,%s,%s)",
                    (subject_id, json.dumps(doc, ensure_ascii=False), narrative, _now()))


def get_doc(subject_id: str, db_path: str = "pox.db"):
    with _connect(db_path) as con:
        r = con.execute("SELECT doc_json FROM profile_v5 WHERE subject_id=%s", (subject_id,)).fetchone()
    return json.loads(r[0]) if r else None


# ── 目的ごとの必要像（生きているもの）──────────────────────────────────────────────
def live_necessities_v5(subject_id: str, db_path: str = "pox.db") -> list:
    """目的ごとの生きている必要像（purpose_id を持つものだけ）。各 dict に sentences・offer_hash を足す。"""
    import necessities as N
    live = [n for n in N.get_live_necessities(subject_id, db_path=db_path)]
    if not live:
        return []
    ids = [n["necessity_id"] for n in live]
    ph = ",".join(["%s"] * len(ids))
    with N._connect(db_path) as con:
        extra = {r[0]: r[1:] for r in con.execute(
            f"SELECT necessity_id, purpose_id, body_json, offer_hash FROM necessities WHERE necessity_id IN ({ph})",
            tuple(ids)).fetchall()}
    out = []
    for n in live:
        pid, body, oh = extra.get(n["necessity_id"], (None, None, None))
        if pid:
            out.append({**n, "purpose_id": pid, "sentences": json.loads(body) if body else [],
                        "offer_hash": oh})
    return out


def latest_event_hash_for_purpose(owner_ref: str, purpose_id: str, db_path: str = "pox.db"):
    """その目的の最新の necessity.published の event_hash（接続の根拠。指示書57 §0-5）。"""
    import ledger_events as le
    last = None
    for e in le.get_events(type_="necessity.published", db_path=db_path):
        p = e["payload"]
        if p.get("owner_ref") == owner_ref and p.get("purpose_id") == purpose_id:
            last = e["event_hash"]
    return last


# ── 文単位のベクトル ─────────────────────────────────────────────────────────────
def save_sentence_vectors(ref_kind: str, ref_id: str, texts: list, model_tag: str, embed_fn,
                          db_path: str = "pox.db") -> int:
    """文ごとに埋め込んで保存する。ref_kind: "necessity"（query）／"offer"（passage）。戻り値＝本数。"""
    role = "query" if ref_kind == "necessity" else "passage"
    with _connect(db_path) as con:
        for i, t in enumerate(texts):
            vec = embed_fn(t, role)
            con.execute("DELETE FROM sentence_vectors WHERE ref_kind=%s AND ref_id=%s AND idx=%s AND model_tag=%s",
                        (ref_kind, ref_id, i, model_tag))
            con.execute("INSERT INTO sentence_vectors (ref_kind, ref_id, idx, model_tag, text, vec) "
                        "VALUES (%s,%s,%s,%s,%s,%s)", (ref_kind, ref_id, i, model_tag, t, json.dumps(vec)))
    return len(texts)


def get_sentence_vectors(ref_kind: str, ref_id: str, model_tag: str, db_path: str = "pox.db") -> list:
    """[(text, vec), ...]（idx 順）。無ければ空。"""
    with _connect(db_path) as con:
        rows = con.execute("SELECT text, vec FROM sentence_vectors WHERE ref_kind=%s AND ref_id=%s "
                           "AND model_tag=%s ORDER BY idx", (ref_kind, ref_id, model_tag)).fetchall()
    return [(r[0], json.loads(r[1])) for r in rows]
