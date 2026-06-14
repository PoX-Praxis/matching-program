#!/usr/bin/env python3
import json, sqlite3, uuid
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS messages "
        "(id TEXT PRIMARY KEY, from_id TEXT NOT NULL, to_id TEXT NOT NULL, "
        "body TEXT NOT NULL, created_at TEXT NOT NULL, is_read INTEGER DEFAULT 0)"
    )
    con.commit()
    return con


def send_message(from_id: str, to_id: str, body: str, db_path: str = "pox.db") -> dict:
    msg_id = f"msg_{uuid.uuid4().hex[:10]}"
    created_at = _now()
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO messages (id, from_id, to_id, body, created_at, is_read) VALUES (?, ?, ?, ?, ?, 0)",
            (msg_id, from_id, to_id, body, created_at),
        )
    return {"id": msg_id, "from_id": from_id, "to_id": to_id, "body": body, "created_at": created_at}


def get_conversation(my_id: str, other_id: str, db_path: str = "pox.db") -> list:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT id, from_id, to_id, body, created_at, is_read FROM messages "
            "WHERE (from_id=? AND to_id=?) OR (from_id=? AND to_id=?) "
            "ORDER BY created_at ASC",
            (my_id, other_id, other_id, my_id),
        ).fetchall()
        con.execute(
            "UPDATE messages SET is_read=1 WHERE to_id=? AND from_id=? AND is_read=0",
            (my_id, other_id),
        )
    return [
        {"id": r[0], "from_id": r[1], "to_id": r[2], "body": r[3], "created_at": r[4], "is_read": r[5]}
        for r in rows
    ]


def get_inbox_summary(my_id: str, db_path: str = "pox.db") -> list:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT from_id, to_id, body, created_at, is_read FROM messages "
            "WHERE from_id=? OR to_id=? ORDER BY created_at DESC",
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
            "SELECT COUNT(*) FROM messages WHERE to_id=? AND is_read=0", (my_id,)
        ).fetchone()
    return row[0] if row else 0


def get_community_messages(community_id: str, db_path: str = "pox.db") -> list:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT id, from_id, to_id, body, created_at, is_read FROM messages "
            "WHERE to_id=? ORDER BY created_at ASC",
            (community_id,),
        ).fetchall()
    return [
        {"id": r[0], "from_id": r[1], "to_id": r[2], "body": r[3], "created_at": r[4], "is_read": r[5]}
        for r in rows
    ]
