#!/usr/bin/env python3
"""
PoX DB層 — seeker（非公開原本）と profile_view（公開表示）の二層保存。

seekers テーブル : 既存互換。load_all_seekers / save_seeker はそのまま残す。
profiles テーブル: save_profile で両カラムを同一トランザクションで書く。
                   閲覧系は get_profile_view のみ使う（seeker は外に出ない）。
"""
import json, sqlite3
from datetime import datetime, timezone
from profile_view import build_profile_view


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS seekers "
        "(id TEXT PRIMARY KEY, seeker_json TEXT NOT NULL)"
    )
    con.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            user_id      TEXT PRIMARY KEY,
            seeker       TEXT NOT NULL,
            profile_view TEXT NOT NULL,
            visibility   TEXT NOT NULL DEFAULT 'public',
            updated_at   TEXT NOT NULL
        )
    """)
    con.commit()
    return con


# ── 既存 API（後方互換・マッチング内部用） ─────────────────────

def save_seeker(seeker_id: str, seeker: dict, db_path: str = "pox.db") -> None:
    """後方互換。save_profile も同時に呼ぶことで profiles テーブルも更新される。"""
    with _connect(db_path) as con:
        con.execute(
            "INSERT OR REPLACE INTO seekers (id, seeker_json) VALUES (?, ?)",
            (seeker_id, json.dumps(seeker, ensure_ascii=False)),
        )


def load_all_seekers(db_path: str = "pox.db") -> list[dict]:
    """全 seeker を [{id, seeker}] で返す。マッチング内部・一覧表示用。"""
    with _connect(db_path) as con:
        rows = con.execute("SELECT id, seeker_json FROM seekers").fetchall()
    return [{"id": row[0], "seeker": json.loads(row[1])} for row in rows]


# ── 二層保存（メイン保存口）────────────────────────────────────

def save_profile(user_id: str, seeker: dict, db_path: str = "pox.db") -> None:
    """
    seeker を保存し、同一トランザクションで profile_view を必ず再生成する。
    seekers テーブルも同時更新（後方互換を保つ）。
    この関数が唯一の保存入口。
    """
    pv  = build_profile_view(seeker)
    now = _now()
    seeker_json = json.dumps(seeker, ensure_ascii=False)
    pv_json     = json.dumps(pv,     ensure_ascii=False)
    with _connect(db_path) as con:
        con.execute(
            "INSERT OR REPLACE INTO seekers (id, seeker_json) VALUES (?, ?)",
            (user_id, seeker_json),
        )
        con.execute(
            "INSERT OR REPLACE INTO profiles "
            "(user_id, seeker, profile_view, visibility, updated_at) "
            "VALUES (?, ?, ?, 'public', ?)",
            (user_id, seeker_json, pv_json, now),
        )


# ── 読み出し（用途別）────────────────────────────────────────

def get_profile_view(user_id: str, db_path: str = "pox.db") -> dict | None:
    """
    profile_view のみ返す。閲覧API・マイページ専用。seeker は絶対に返さない。
    profiles テーブルに無い場合は seekers テーブルから自動生成（後方互換）。
    """
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT profile_view FROM profiles WHERE user_id=?", (user_id,)
        ).fetchone()
        if row:
            return json.loads(row[0])
        # 後方互換フォールバック: seekers テーブルから生成
        seeker_row = con.execute(
            "SELECT seeker_json FROM seekers WHERE id=?", (user_id,)
        ).fetchone()
        if seeker_row:
            return build_profile_view(json.loads(seeker_row[0]))
    return None


def get_seeker(user_id: str, db_path: str = "pox.db") -> dict | None:
    """
    seeker のみ返す。マッチングAPI専用。閲覧系から呼ばない。
    profiles → seekers の順でフォールバック。
    """
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT seeker FROM profiles WHERE user_id=?", (user_id,)
        ).fetchone()
        if row:
            return json.loads(row[0])
        row = con.execute(
            "SELECT seeker_json FROM seekers WHERE id=?", (user_id,)
        ).fetchone()
    return json.loads(row[0]) if row else None


def list_candidate_pool(exclude_user_id: str, db_path: str = "pox.db") -> list[dict]:
    """
    他全員の {id, profile: str} を返す。run_matching の candidate_pool 用。
    seeker の内容はプロフィール文字列に変換済み（外には出ない）。
    """
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT user_id, seeker FROM profiles WHERE user_id != ?",
            (exclude_user_id,),
        ).fetchall()
        if rows:
            return [{"id": r[0], "profile": _to_profile_text(json.loads(r[1]))} for r in rows]
        # フォールバック: seekers テーブル
        rows = con.execute(
            "SELECT id, seeker_json FROM seekers WHERE id != ?",
            (exclude_user_id,),
        ).fetchall()
    return [{"id": r[0], "profile": _to_profile_text(json.loads(r[1]))} for r in rows]


def _to_profile_text(seeker: dict) -> str:
    return "。".join(seeker[k] for k in ("意志", "求めている", "能力") if seeker.get(k))
