#!/usr/bin/env python3
"""
①v5（目的ごとの必要像・与え像・文単位）の受信・保存（指示書57 段2）。

- 受信の検証規則（文面 2026-10-06 の 10 項目＋改訂2 の 11・12。指示書60）: validate(doc)
- 目的の id はサーバーが振る（不変）。①の purpose_id は対応付けのヒントにだけ使う
- 与え像は版ごとに通常DB（offers）。offer_hash を各目的の必要像の c3 ハッシュに含める（ピン留め・案 A）
- 文単位のベクトル（sentence_vectors）: 必要像の文（query）と与え像の文（passage）を個別に埋め込む
- v4 互換: v4 の人は「目的 1 つ・与え像なし」として読む（与え像の代わりに現状で照合する）
"""
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone

from canon import canonicalize, sha256_hex
from db_connect import get_connection, is_postgres
from match_config import ALPHA_DEFAULT, BETA_DEFAULT

TYPES = ("向かう先", "関わり方", "資源", "関心")
MAX_PURPOSES, MAX_SENTENCES, MAX_REQUIRED = 3, 5, 2
# 求人票の文体（規則 8）
FORBIDDEN = ("年以上", "必須スキル", "ができる方", "を募集")
# 「向かう先」への手段の混入（規則 3）。誤って弾かないよう最小限の語だけを見る。
MEANS_MARKERS = ("そのために", "手段として")
DROP_KEYS = ("p_sharpness", "gamma", "alpha", "beta")   # 規則 7: あれば捨てる（alpha・beta は指示書61 で追加）
# 規則 14: どのプロンプトで作ったか（_meta.source）。無いもの＝改訂2（"v5r2"）として扱う（拒否しない）。
SOURCES = ("v5r3-A", "v5r3-B", "v5r2-A", "v5r2-B")
SOURCE_DEFAULT = "v5r2"
# 規則 9: 根拠が「／」「/」・改行でつながれていたら、比較のときだけ分ける（保存は原文のまま）。
# 改行は、AI が複数の引用を "\n" でつないで出した実例（2026-10）による。各部分が生テキストにあれば通す。
_EVIDENCE_SPLIT = re.compile(r"[／/\r\n]")


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


# ── 受信の検証（改訂2 §3。指示書60）──────────────────────────────────────────────
def _norm(s: str) -> str:
    """検証9 の比較用の派生キー（**比較のときだけ**使う。保存はしない＝指示書39 §1-2）。
    Unicode NFKC（全角半角の統一）→ 空白・改行・タブの除去 → 句読点（Unicode の P 類）の除去。"""
    t = unicodedata.normalize("NFKC", str(s or ""))
    return "".join(c for c in t if not c.isspace() and not unicodedata.category(c).startswith("P"))


def raw_texts(doc: dict) -> list:
    """supporting_material.生テキスト（本人の素の言葉。根拠の検査用・本人のみ）。文字列 1 つでも受ける。"""
    raw = ((doc or {}).get("supporting_material") or {}).get("生テキスト")
    if isinstance(raw, str):
        raw = [raw]
    return [str(x) for x in raw] if isinstance(raw, list) else []


def validate(doc: dict):
    """検証規則（改訂2 §3）。通らなければ (False, 理由 1 行)。通れば (True, doc)。

    1 schema_version / 2 目的の数 / 3 向かう先と手段（混入）/ 4 必要像の文数・必須の数 / 5 与え像の文数（0〜5）/
    6 型 / 7 p_sharpness・gamma は捨てる（拒否しない）/ 8 求人票の文体（根拠＝本人の引用は対象外）/
    9 根拠が生テキストに実在（正規化した派生キーで比較）/ 10 purpose_id はヒント（assign_purposes が振り直す）/
    11 一行紹介・要約文・生テキストが空でない / 12 id（ハンドル）がある /
    13 generator が空でない / 14 _meta.source が既知の値か、無い（無ければ改訂2 として扱う）
    """
    if not is_v5(doc):
        return False, "schema_version が v5 ではありません"
    if not str(doc.get("id") or "").strip():
        return False, "id（ハンドル）がありません"
    if not str(doc.get("generator") or "").strip():
        return False, "generator（AI の名前）が入っていません"
    meta = doc.get("_meta")
    src = meta.get("source") if isinstance(meta, dict) else None
    if src is not None and src not in SOURCES:
        return False, f"_meta.source は {'／'.join(SOURCES)} のいずれかです"
    sm = doc.get("supporting_material") if isinstance(doc.get("supporting_material"), dict) else {}
    texts = raw_texts(doc)
    for k in ("一行紹介", "要約文"):
        if not str(sm.get(k) or "").strip():
            return False, f"supporting_material の「{k}」がありません"
    if not any(t.strip() for t in texts):
        return False, "supporting_material の「生テキスト」がありません"
    purposes = doc.get("purposes")
    if not isinstance(purposes, list) or not (1 <= len(purposes) <= MAX_PURPOSES):
        return False, f"目的（purposes）は 1〜{MAX_PURPOSES} 件にしてください"
    story = _norm("".join(texts))
    for k in DROP_KEYS:                                   # 規則 7: どこにあっても捨てる
        doc.pop(k, None)
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
        for part in (_EVIDENCE_SPLIT.split(ev) if ev else []):
            if _norm(part) and _norm(part) not in story:
                head = part.strip()
                head = head[:20] + ("…" if len(head) > 20 else "")
                return False, f"目的 {i}: 根拠「{head}」が生テキストに見つかりません（引用は原文のまま）"
        for k in DROP_KEYS:
            p.pop(k, None)
            if isinstance(p.get("数値"), dict):
                p["数値"].pop(k, None)
    offers = doc.get("与え像", [])
    if not isinstance(offers, list) or len(offers) > MAX_SENTENCES:
        return False, f"与え像は 0〜{MAX_SENTENCES} 文にしてください"
    for x in offers:
        if not isinstance(x, dict) or not str(x.get("文") or "").strip():
            return False, "与え像の文が空です"
        if x.get("型") not in TYPES:
            return False, f"与え像の型は {'／'.join(TYPES)} のいずれかです"
    # 規則 8 は AI が書いた文（向かう先・手段・必要像・与え像）だけを見る。根拠は本人の言葉の引用なので対象外。
    written = [{k: v for k, v in p.items() if k not in ("根拠", "数値")} for p in purposes]
    blob = json.dumps({"p": written, "o": offers}, ensure_ascii=False)
    for w in FORBIDDEN:
        if w in blob:
            return False, f"求人票の文体（「{w}」）は使えません"
    return True, doc


def source_of(doc: dict) -> str:
    """どのプロンプトで作ったか（_meta.source）。無ければ改訂2（"v5r2"）。"""
    meta = (doc or {}).get("_meta")
    return (meta.get("source") if isinstance(meta, dict) else None) or SOURCE_DEFAULT


def keep_offers(doc: dict, keep) -> dict:
    """下書きの確認で本人が残した与え像だけにする（指示書60 §3）。keep は残す文の番号（0 始まり）の列。
    **外すだけ**（足す・書き換えはできない）。範囲外・重複は無視し、①の順を保つ。keep が None なら全部残す。"""
    if keep is None:
        return doc
    offers = list(doc.get("与え像") or [])
    idx = {int(i) for i in keep if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(offers)}
    return {**doc, "与え像": [x for i, x in enumerate(offers) if i in idx]}


# ── v4 の受付に渡す形（profiles_v4 と全文ベクトルの互換）─────────────────────────
def to_flat(doc: dict) -> dict:
    """v5 を、既存の v4 受付（profiles_v4・全文ベクトル・スナップショット）に渡すフラットな形にする。

    全文ベクトル（互換）は最初の目的の必要像で作る。目的ごとの照合は文単位ベクトルで行う。
    """
    purposes = doc.get("purposes") or []
    first = purposes[0] if purposes else {}
    state = doc.get("現状") or {}
    sm = dict(doc.get("supporting_material") or {})       # 生テキスト・一行紹介・要約文などは原文のまま渡す
    nums = first.get("数値") or {}
    return {
        "will_text": "\n".join(str(p.get("向かう先") or "") for p in purposes),
        "state_have": state.get("持っているもの", ""), "state_can_type": state.get("できること_型", ""),
        "state_bound": state.get("縛られているもの", ""), "state_unsorted": state.get("未分類", ""),
        "supporting_raw": sm,
        "necessity_text": "\n".join(str(x.get("文") or "") for x in (first.get("必要像") or [])),
        "gate_s": nums.get("gate_s"), "gate_u": nums.get("gate_u"), "p_sharpness": 0.0,
        # v4 互換の全文ベクトルの行（derived_necessity）は alpha・beta を必須とするので既定値を入れる。
        # v5 の照合（段3・共鳴の門）は使わない。①改訂3 は出さず、改訂2 の値は規則 7 で捨てている。
        "alpha": ALPHA_DEFAULT, "beta": BETA_DEFAULT,
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
                          db_path: str = "pox.db", role: str = None, indexes: list = None) -> int:
    """文ごとに埋め込んで保存する。戻り値＝本数。
    ref_kind: "necessity"（query）／"offer"（passage）／"dest"＝目的の向かう先（symmetric。共鳴の門・指示書61）／
    "state"＝現状の欄（passage。idx は欄の番号。表示で該当する欄を選ぶため）。
    indexes を渡すと idx をその番号にする（既定は 0, 1, …）。"""
    role = role or {"necessity": "query", "dest": "symmetric"}.get(ref_kind, "passage")
    idxs = list(indexes) if indexes is not None else list(range(len(texts)))
    with _connect(db_path) as con:
        for i, t in zip(idxs, texts):
            vec = embed_fn(t, role)
            con.execute("DELETE FROM sentence_vectors WHERE ref_kind=%s AND ref_id=%s AND idx=%s AND model_tag=%s",
                        (ref_kind, ref_id, i, model_tag))
            con.execute("INSERT INTO sentence_vectors (ref_kind, ref_id, idx, model_tag, text, vec) "
                        "VALUES (%s,%s,%s,%s,%s,%s)", (ref_kind, ref_id, i, model_tag, t, json.dumps(vec)))
    return len(texts)


def delete_sentence_vectors(ref_kind: str, ref_id: str, model_tag: str, keep: list = None,
                            db_path: str = "pox.db") -> None:
    """ref の文ベクトルを消す（keep の idx は残す）。現状の欄が空になったときの掃除用。"""
    with _connect(db_path) as con:
        rows = con.execute("SELECT idx FROM sentence_vectors WHERE ref_kind=%s AND ref_id=%s AND model_tag=%s",
                           (ref_kind, ref_id, model_tag)).fetchall()
        for (i,) in rows:
            if keep is None or i not in keep:
                con.execute("DELETE FROM sentence_vectors WHERE ref_kind=%s AND ref_id=%s AND idx=%s "
                            "AND model_tag=%s", (ref_kind, ref_id, i, model_tag))


def get_sentence_vectors(ref_kind: str, ref_id: str, model_tag: str, db_path: str = "pox.db") -> list:
    """[(text, vec), ...]（idx 順）。無ければ空。"""
    return [(t, v) for _, (t, v) in get_sentence_vectors_indexed(ref_kind, ref_id, model_tag, db_path=db_path)]


def get_sentence_vectors_indexed(ref_kind: str, ref_id: str, model_tag: str, db_path: str = "pox.db") -> list:
    """[(idx, (text, vec)), ...]（idx 順）。"""
    with _connect(db_path) as con:
        rows = con.execute("SELECT idx, text, vec FROM sentence_vectors WHERE ref_kind=%s AND ref_id=%s "
                           "AND model_tag=%s ORDER BY idx", (ref_kind, ref_id, model_tag)).fetchall()
    return [(r[0], (r[1], json.loads(r[2]))) for r in rows]


def necessity_body(necessity_id: str, db_path: str = "pox.db"):
    """必要像の版の文（必須・型つき）と門の値。無ければ None。"""
    import necessities as N
    with N._connect(db_path) as con:
        r = con.execute("SELECT body_json, gate_s, gate_u, purpose_id FROM necessities WHERE necessity_id=%s",
                        (necessity_id,)).fetchone()
    if not r:
        return None
    return {"sentences": json.loads(r[0]) if r[0] else [], "gate_s": r[1], "gate_u": r[2], "purpose_id": r[3]}


def save_dest_vectors(subject_id: str, model_tag: str, embed_fn, db_path: str = "pox.db") -> int:
    """生きている目的の向かう先を埋め込む（共鳴の門。指示書61）。文が変わった目的だけ作り直す。"""
    doc = get_doc(subject_id, db_path=db_path) or {}
    dest = {p.get("purpose_id"): str(p.get("向かう先") or "") for p in (doc.get("purposes") or [])}
    made = 0
    for n in live_necessities_v5(subject_id, db_path=db_path):
        pid, text = n["purpose_id"], dest.get(n["purpose_id"], "")
        if not text:
            continue
        cur = get_sentence_vectors("dest", pid, model_tag, db_path=db_path)
        if not cur or cur[0][0] != text:
            made += save_sentence_vectors("dest", pid, [text], model_tag, embed_fn, db_path=db_path)
    return made


def save_state_slot_vectors(subject_id: str, slots: list, model_tag: str, embed_fn,
                            db_path: str = "pox.db") -> int:
    """現状の欄ごとのベクトル（表示で「該当する欄」を選ぶ。判定には使わない）。slots は 4 欄の文（空は飛ばす）。
    文が変わった欄だけ作り直し、空になった欄は消す。"""
    have = dict(get_sentence_vectors_indexed("state", subject_id, model_tag, db_path=db_path))
    made, keep = 0, []
    for i, t in enumerate(slots):
        t = str(t or "").strip()
        if not t or t == "未取得":
            continue
        keep.append(i)
        if i not in have or have[i][0] != t:
            made += save_sentence_vectors("state", subject_id, [t], model_tag, embed_fn,
                                          db_path=db_path, indexes=[i])
    delete_sentence_vectors("state", subject_id, model_tag, keep=keep, db_path=db_path)
    return made
