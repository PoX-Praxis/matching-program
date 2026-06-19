#!/usr/bin/env python3
"""
PoX データ移行スクリプト — SQLite → PostgreSQL。

冪等（ON CONFLICT DO UPDATE）なので何度流しても重複しない。
本番デプロイのたびには実行しない。手動で一度だけ。

前提:
  - DATABASE_URL が PostgreSQL を指している
  - SQLite の pox.db（または POX_DB）がローカルにある
  - init_schema.py を先に実行してテーブルが存在すること

使い方:
  DATABASE_URL=postgresql://... python scripts/migrate_sqlite_to_pg.py
  DATABASE_URL=postgresql://... POX_DB=path/to/pox.db python scripts/migrate_sqlite_to_pg.py
"""
import sys, os, json, sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db_connect import get_connection, is_postgres, DATABASE_URL

if not is_postgres():
    print("ERROR: DATABASE_URL が設定されていません。PostgreSQL 移行先を指定してください。")
    sys.exit(1)

SQ_PATH = os.environ.get("POX_DB", "pox.db")
if not os.path.exists(SQ_PATH):
    print(f"ERROR: SQLite ファイルが見つかりません: {SQ_PATH}")
    sys.exit(1)

sq = sqlite3.connect(SQ_PATH)


def _rows(sql):
    return sq.execute(sql).fetchall()


def migrate():
    print(f"SQLite: {SQ_PATH}")
    print(f"Postgres: {DATABASE_URL[:40]}...")

    with get_connection() as con:
        # seekers
        rows = _rows("SELECT id, seeker_json FROM seekers")
        for id_, seeker_json in rows:
            con.execute(
                "INSERT INTO seekers (id, seeker_json) VALUES (%s, %s) "
                "ON CONFLICT (id) DO UPDATE SET seeker_json = EXCLUDED.seeker_json",
                (id_, seeker_json),
            )
        print(f"  seekers: {len(rows)} 件")

        # profiles（view_overrides / created_at は NULL 許容で互換）
        cols_q = sq.execute("PRAGMA table_info(profiles)").fetchall()
        col_names = [c[1] for c in cols_q]
        rows = sq.execute("SELECT " + ", ".join(col_names) + " FROM profiles").fetchall()
        for r in rows:
            d = dict(zip(col_names, r))
            con.execute(
                """
                INSERT INTO profiles
                    (user_id, seeker, profile_view, visibility, view_overrides, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    seeker       = EXCLUDED.seeker,
                    profile_view = EXCLUDED.profile_view,
                    visibility   = EXCLUDED.visibility,
                    view_overrides = EXCLUDED.view_overrides,
                    updated_at   = EXCLUDED.updated_at
                """,
                (
                    d["user_id"], d["seeker"], d["profile_view"],
                    d.get("visibility", "public"), d.get("view_overrides", "{}"),
                    d.get("created_at"), d.get("updated_at", ""),
                ),
            )
        print(f"  profiles: {len(rows)} 件")

        # vessels
        rows = _rows("SELECT vessel_id, vessel_json FROM vessels")
        for vessel_id, vessel_json in rows:
            con.execute(
                "INSERT INTO vessels (vessel_id, vessel_json) VALUES (%s, %s) "
                "ON CONFLICT (vessel_id) DO UPDATE SET vessel_json = EXCLUDED.vessel_json",
                (vessel_id, vessel_json),
            )
        print(f"  vessels: {len(rows)} 件")

        # messages
        rows = _rows(
            "SELECT id, from_id, to_id, body, created_at, is_read, attachment_url FROM messages"
        )
        for r in rows:
            con.execute(
                "INSERT INTO messages (id, from_id, to_id, body, created_at, is_read, attachment_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET body = EXCLUDED.body, is_read = EXCLUDED.is_read",
                r,
            )
        print(f"  messages: {len(rows)} 件")

        # communities
        rows = _rows("SELECT id, name, description, founder, created_at FROM communities")
        for r in rows:
            con.execute(
                "INSERT INTO communities (id, name, description, founder, created_at) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description",
                r,
            )
        print(f"  communities: {len(rows)} 件")

        # community_members
        rows = _rows("SELECT community_id, member_id, status, joined_at FROM community_members")
        for r in rows:
            con.execute(
                "INSERT INTO community_members (community_id, member_id, status, joined_at) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (community_id, member_id) DO UPDATE SET status = EXCLUDED.status",
                r,
            )
        print(f"  community_members: {len(rows)} 件")

    sq.close()
    print("移行完了。")


if __name__ == "__main__":
    migrate()
