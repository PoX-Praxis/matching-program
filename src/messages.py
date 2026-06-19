#!/usr/bin/env python3
import json, uuid
from datetime import datetime, timezone
from db_connect import get_connection, is_postgres


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS messages "
            "(id TEXT PRIMARY KEY, from_id TEXT NOT NULL, to_id TEXT NOT NULL, "
            "body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
            "is_read INTEGER DEFAULT 0, attachment_url TEXT)"
        )
        # 旧 DB への attachment_url 列追加（既存なら無視）
        try:
            con.execute("ALTER TABLE messages ADD COLUMN attachment_url TEXT")
        except Exception:
            pass
        con.commit()
    return con


def send_message(from_id: str, to_id: str, body: str, attachment_url: str = None, db_path: str = "pox.db") -> dict:
    msg_id = f"msg_{uuid.uuid4().hex[:10]}"
    created_at = _now()
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO messages (id, from_id, to_id, body, created_at, is_read, attachment_url) "
            "VALUES (%s, %s, %s, %s, %s, 0, %s)",
            (msg_id, from_id, to_id, body, created_at, attachment_url),
        )
    return {"id": msg_id, "from_id": from_id, "to_id": to_id, "body": body,
            "created_at": created_at, "attachment_url": attachment_url}


def get_conversation(my_id: str, other_id: str, db_path: str = "pox.db") -> list:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT id, from_id, to_id, body, created_at, is_read, attachment_url FROM messages "
            "WHERE (from_id=%s AND to_id=%s) OR (from_id=%s AND to_id=%s) "
            "ORDER BY created_at ASC",
            (my_id, other_id, other_id, my_id),
        ).fetchall()
        con.execute(
            "UPDATE messages SET is_read=1 WHERE to_id=%s AND from_id=%s AND is_read=0",
            (my_id, other_id),
        )
    return [
        {"id": r[0], "from_id": r[1], "to_id": r[2], "body": r[3],
         "created_at": r[4], "is_read": r[5], "attachment_url": r[6]}
        for r in rows
    ]


def get_inbox_summary(my_id: str, db_path: str = "pox.db") -> list:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT from_id, to_id, body, created_at, is_read FROM messages "
            "WHERE from_id=%s OR to_id=%s ORDER BY created_at DESC",
            (my_id, my_id),
        ).fetchall()

    convs: dict[str, dict] = {}
    for from_id, to_id, body, created_at, is_read in rows:
        other = to_id if from_id == my_id else from_id
        if other not in convs:
            convs[other] = {"other_id": other, "last_body": body, "last_at": created_at, "unread": 0}
        if to_id == my_id and not is_read:
            convs[other]["unread"] += 1

    return sorted(convs.values(), key=lambda x: x["last_at"], reverse=True)


def get_unread_count(my_id: str, db_path: str = "pox.db") -> int:
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT COUNT(*) FROM messages WHERE to_id=%s AND is_read=0", (my_id,)
        ).fetchone()
    return row[0] if row else 0


def get_community_messages(community_id: str, db_path: str = "pox.db") -> list:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT id, from_id, to_id, body, created_at, is_read, attachment_url FROM messages "
            "WHERE to_id=%s ORDER BY created_at ASC",
            (community_id,),
        ).fetchall()
    return [
        {"id": r[0], "from_id": r[1], "to_id": r[2], "body": r[3],
         "created_at": r[4], "is_read": r[5], "attachment_url": r[6]}
        for r in rows
    ]
