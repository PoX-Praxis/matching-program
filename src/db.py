#!/usr/bin/env python3
"""
PoX DB層 — seeker（非公開原本）と profile_view（公開表示）の二層保存。

seekers テーブル : 既存互換。load_all_seekers / save_seeker はそのまま残す。
profiles テーブル: save_profile で両カラムを同一トランザクションで書く（UPSERT）。
                   閲覧系は get_profile_view のみ使う（seeker は外に出ない）。
                   view_overrides は表示専用設定（§5）。閲覧時に profile_view へ重ねる。

接続: get_connection(db_path) を使用。DATABASE_URL があれば Postgres、なければ SQLite。
SQL : %s プレースホルダ統一（SQLite では db_connect が内部で ? に変換）。
"""
import json
from datetime import datetime, timezone
from db_connect import get_connection, is_postgres
from profile_view import build_profile_view, apply_overrides


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str = "pox.db"):
    """接続を返す。SQLite の場合はこのモジュールのテーブルも初期化する。"""
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS seekers "
            "(id TEXT PRIMARY KEY, seeker_json TEXT NOT NULL)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id        TEXT PRIMARY KEY,
                seeker         TEXT NOT NULL,
                profile_view   TEXT NOT NULL,
                visibility     TEXT NOT NULL DEFAULT 'public',
                view_overrides TEXT NOT NULL DEFAULT '{}',
                created_at     TEXT,
                updated_at     TEXT NOT NULL DEFAULT ''
            )
        """)
        _migrate_sqlite(con)
        con.commit()
    return con


def _migrate_sqlite(con) -> None:
    """SQLite 専用: カラムが無ければ追加（既存ユーザーの DB 互換性）。"""
    for sql in (
        "ALTER TABLE profiles ADD COLUMN view_overrides TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE profiles ADD COLUMN created_at TEXT",
    ):
        try:
            con.execute(sql)
        except Exception:
            pass


# ── 既存 API（後方互換・マッチング内部用） ─────────────────────

def save_seeker(seeker_id: str, seeker: dict, db_path: str = "pox.db") -> None:
    """後方互換。save_profile も同時に呼ぶことで profiles テーブルも更新される。"""
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO seekers (id, seeker_json) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET seeker_json = EXCLUDED.seeker_json",
            (seeker_id, json.dumps(seeker, ensure_ascii=False)),
        )


def load_all_seekers(db_path: str = "pox.db") -> list[dict]:
    """全 seeker を [{id, seeker}] で返す。マッチング内部・一覧表示用。"""
    with _connect(db_path) as con:
        rows = con.execute("SELECT id, seeker_json FROM seekers").fetchall()
    return [{"id": row[0], "seeker": json.loads(row[1])} for row in rows]


# ── 二層保存（メイン保存口・UPSERT §4）─────────────────────────

def save_profile(user_id: str, seeker: dict, db_path: str = "pox.db") -> None:
    """
    seeker を保存し、同一トランザクションで profile_view を必ず再生成する（UPSERT）。
    既存 user_id は上書き（新アカウントを増やさない §4）。
    created_at は新規時のみ・view_overrides は更新時に保持（表示の手直しが消えない §5.1）。
    seekers テーブルも同時更新（後方互換）。この関数が唯一の seeker 保存入口。
    """
    pv  = build_profile_view(seeker)
    now = _now()
    seeker_json = json.dumps(seeker, ensure_ascii=False)
    pv_json     = json.dumps(pv,     ensure_ascii=False)
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO seekers (id, seeker_json) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET seeker_json = EXCLUDED.seeker_json",
            (user_id, seeker_json),
        )
        con.execute(
            """
            INSERT INTO profiles
                (user_id, seeker, profile_view, visibility, view_overrides, created_at, updated_at)
            VALUES (%s, %s, %s, 'public', '{}', %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                seeker       = EXCLUDED.seeker,
                profile_view = EXCLUDED.profile_view,
                updated_at   = EXCLUDED.updated_at
            """,
            (user_id, seeker_json, pv_json, now, now),
        )


# ── 読み出し（用途別）────────────────────────────────────────

def get_profile_view(user_id: str, db_path: str = "pox.db") -> dict | None:
    """
    公開表示用 profile_view を返す（view_overrides を重ねた結果）。seeker は絶対に返さない。
    profiles に無い場合は seekers から自動生成（後方互換）。
    """
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT profile_view, view_overrides FROM profiles WHERE user_id = %s", (user_id,)
        ).fetchone()
        if row:
            pv = json.loads(row[0])
            overrides = json.loads(row[1] or "{}")
            return apply_overrides(pv, overrides)
        seeker_row = con.execute(
            "SELECT seeker_json FROM seekers WHERE id = %s", (user_id,)
        ).fetchone()
        if seeker_row:
            return build_profile_view(json.loads(seeker_row[0]))
    return None


def get_profile_edit_data(user_id: str, db_path: str = "pox.db") -> dict | None:
    """
    「見せ方を編集」用。オーバーレイ前の base profile_view と現在の overrides を返す。
    （所有者の編集用。seeker 原文は返さない。）
    """
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT profile_view, view_overrides FROM profiles WHERE user_id = %s", (user_id,)
        ).fetchone()
        if row:
            return {"base": json.loads(row[0]), "overrides": json.loads(row[1] or "{}")}
        seeker_row = con.execute(
            "SELECT seeker_json FROM seekers WHERE id = %s", (user_id,)
        ).fetchone()
        if seeker_row:
            return {"base": build_profile_view(json.loads(seeker_row[0])), "overrides": {}}
    return None


def save_view_overrides(user_id: str, overrides: dict, db_path: str = "pox.db") -> bool:
    """
    view_overrides のみ更新（表示専用・profile_view 再生成なし §5.1）。
    profiles 行が無い旧アカウントは seekers から実体化してから設定する。
    """
    ov_json = json.dumps(overrides, ensure_ascii=False)
    now = _now()
    with _connect(db_path) as con:
        cur = con.execute(
            "UPDATE profiles SET view_overrides = %s, updated_at = %s WHERE user_id = %s",
            (ov_json, now, user_id),
        )
        if cur.rowcount:
            return True
        seeker_row = con.execute(
            "SELECT seeker_json FROM seekers WHERE id = %s", (user_id,)
        ).fetchone()
        if seeker_row is None:
            return False
        seeker = json.loads(seeker_row[0])
        pv_json = json.dumps(build_profile_view(seeker), ensure_ascii=False)
        con.execute(
            """
            INSERT INTO profiles
                (user_id, seeker, profile_view, visibility, view_overrides, created_at, updated_at)
            VALUES (%s, %s, %s, 'public', %s, %s, %s)
            """,
            (user_id, seeker_row[0], pv_json, ov_json, now, now),
        )
    return True


def update_seeker_core(user_id: str, fields: dict, db_path: str = "pox.db") -> bool:
    """
    「中身を編集」用。4軸（意志/求めている/能力/フェーズ）のみ更新し supporting_material は保持。
    save_profile 経由で profile_view を再生成する（§5.1）。
    """
    seeker = get_seeker(user_id, db_path=db_path)
    if seeker is None:
        return False
    for k in ("意志", "求めている", "能力", "フェーズ"):
        if k in fields:
            seeker[k] = fields[k]
    save_profile(user_id, seeker, db_path=db_path)
    return True


def get_seeker(user_id: str, db_path: str = "pox.db") -> dict | None:
    """
    seeker のみ返す。マッチングAPI・内部更新専用。閲覧系から呼ばない。
    profiles → seekers の順でフォールバック。
    """
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT seeker FROM profiles WHERE user_id = %s", (user_id,)
        ).fetchone()
        if row:
            return json.loads(row[0])
        row = con.execute(
            "SELECT seeker_json FROM seekers WHERE id = %s", (user_id,)
        ).fetchone()
    return json.loads(row[0]) if row else None


def list_candidate_pool(exclude_user_id: str, db_path: str = "pox.db") -> list[dict]:
    """
    他全員の {id, profile: str} を返す。run_matching の candidate_pool 用。
    seeker の内容はプロフィール文字列に変換済み（外には出ない）。
    """
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT user_id, seeker FROM profiles WHERE user_id != %s",
            (exclude_user_id,),
        ).fetchall()
        if rows:
            return [{"id": r[0], "profile": _to_profile_text(json.loads(r[1]))} for r in rows]
        rows = con.execute(
            "SELECT id, seeker_json FROM seekers WHERE id != %s",
            (exclude_user_id,),
        ).fetchall()
    return [{"id": r[0], "profile": _to_profile_text(json.loads(r[1]))} for r in rows]


def _to_profile_text(seeker: dict) -> str:
    return "。".join(seeker[k] for k in ("意志", "求めている", "能力") if seeker.get(k))
