"""指示書70 の確認（KAORU・Claude 2026-11-11）の運用: 本番の件数だけを読み取りで数える・指示書69 の別の表の行を消す。

- 件数だけを返す（中身・id・文は返さない）。起動のログに出し、KAORU が Render のログで読む
- 指示書69 の別の表（purpose_kinds・explanation_settings）は、構造化の値（c5 の 種類・p4 の 説明への使用）に置き換えた。
  行は、本番の行数を報告した後に消す（POX_OPS_CLEAR_69_TABLES=1 のときだけ。表の定義は次の整理まで残す）
- 台帳には触れない（この 2 つの表は台帳にイベントを書いていない。指示書69 の terms.accepted なども、過去の記録として残す）
"""
import json

from db_connect import get_connection

LEGACY_69_TABLES = ("purpose_kinds", "explanation_settings")
CLEAR_ENV = "POX_OPS_CLEAR_69_TABLES"


def _count(db_path, sql):
    """1 つの数だけ。表が無ければ None（Postgres は失敗した取引を引きずらないよう、数えるごとに接続を分ける）。"""
    try:
        with get_connection(db_path) as con:
            return int(con.execute(sql).fetchone()[0] or 0)
    except Exception:  # noqa: BLE001（表がまだ無い）
        return None


def source_counts(source: str = "v5r4-B", db_path: str = "pox.db") -> dict:
    """_meta.source が source の登録の件数だけ。
    registrations: 確定した下書きの数（登録の回数）／people: その人数／current: いまの登録（profile_v5）がその形の人数。"""
    regs, people, current = 0, set(), 0
    with get_connection(db_path) as con:
        rows = con.execute("SELECT subject_id, payload_json FROM declaration_drafts WHERE status='confirmed'").fetchall()
    for subject_id, payload in rows:
        if _source(payload) == source:
            regs += 1
            people.add(subject_id)
    with get_connection(db_path) as con:
        docs = con.execute("SELECT doc_json FROM profile_v5").fetchall()
    current = sum(1 for (doc,) in docs if _source(doc) == source)
    return {"registrations": regs, "people": len(people), "current": current}


def _source(raw):
    try:
        d = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        return None
    meta = d.get("_meta") if isinstance(d, dict) else None
    return meta.get("source") if isinstance(meta, dict) else None


def legacy_69_counts(db_path: str = "pox.db") -> dict:
    """指示書69 の別の表の行数だけ（表が無ければ None）。"""
    return {t: _count(db_path, f"SELECT count(*) FROM {t}") for t in LEGACY_69_TABLES}


def clear_legacy_69(db_path: str = "pox.db") -> dict:
    """指示書69 の別の表の行を消す（表の定義は残す。台帳には触れない）。消した行数を返す。"""
    before = legacy_69_counts(db_path)
    with get_connection(db_path) as con:
        for t in LEGACY_69_TABLES:
            if before.get(t) is not None:
                con.execute(f"DELETE FROM {t}")
    return before


def startup_lines(env: dict, db_path: str = "pox.db") -> list:
    """起動のログの行（件数だけ）。CLEAR_ENV が "1" なら、数えた後に 69 の表の行を消す。"""
    out = []
    try:
        s = source_counts("v5r4-B", db_path=db_path)
        out.append(f"[ops-70] v5r4-B の登録={s['registrations']}回・{s['people']}人（いまの登録が v5r4-B={s['current']}人）")
    except Exception as e:  # noqa: BLE001
        out.append(f"[ops-70] v5r4-B の登録を数えられませんでした（{type(e).__name__}）")
    try:
        c = legacy_69_counts(db_path)
        out.append("[ops-70] 69 の表の行数 " + "・".join(f"{t}={'なし' if n is None else n}" for t, n in c.items()))
        if env.get(CLEAR_ENV) == "1":
            gone = clear_legacy_69(db_path)
            out.append("[ops-70] 69 の表の行を消しました " + "・".join(f"{t}={n or 0}" for t, n in gone.items())
                       + f"（台帳は触れていません。{CLEAR_ENV} を外してください）")
    except Exception as e:  # noqa: BLE001
        out.append(f"[ops-70] 69 の表を数えられませんでした（{type(e).__name__}）")
    return out
