#!/usr/bin/env python3
import json, sqlite3, uuid
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS communities "
        "(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, "
        "founder TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS community_members "
        "(community_id TEXT NOT NULL, member_id TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending', joined_at TEXT NOT NULL, "
        "PRIMARY KEY (community_id, member_id))"
    )
    con.commit()
    return con


def create_community(founder: str, name: str, description: str, db_path: str = "pox.db") -> dict:
    cid = f"c_{uuid.uuid4().hex[:10]}"
    created_at = _now()
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO communities (id, name, description, founder, created_at) VALUES (?, ?, ?, ?, ?)",
            (cid, name, description or "", founder, created_at),
        )
        con.execute(
            "INSERT INTO community_members (community_id, member_id, status, joined_at) VALUES (?, ?, 'active', ?)",
            (cid, founder, created_at),
        )
    return {"id": cid, "name": name, "description": description or "", "founder": founder, "created_at": created_at}


def get_community(community_id: str, db_path: str = "pox.db") -> dict | None:
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT id, name, description, founder, created_at FROM communities WHERE id=?",
            (community_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "description": row[2], "founder": row[3], "created_at": row[4]}


def get_all_communities(db_path: str = "pox.db") -> list:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT id, name, description, founder, created_at FROM communities ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for r in rows:
            cid = r[0]
            count = con.execute(
                "SELECT COUNT(*) FROM community_members WHERE community_id=? AND status='active'", (cid,)
            ).fetchone()[0]
            result.append({
                "id": cid, "name": r[1], "description": r[2],
                "founder": r[3], "created_at": r[4], "member_count": count,
            })
    return result


def request_join(community_id: str, member_id: str, db_path: str = "pox.db") -> dict:
    with _connect(db_path) as con:
        existing = con.execute(
            "SELECT status FROM community_members WHERE community_id=? AND member_id=?",
            (community_id, member_id),
        ).fetchone()
        if existing:
            return {"community_id": community_id, "member_id": member_id, "status": existing[0]}
        joined_at = _now()
        con.execute(
            "INSERT INTO community_members (community_id, member_id, status, joined_at) VALUES (?, ?, 'pending', ?)",
            (community_id, member_id, joined_at),
        )
    return {"community_id": community_id, "member_id": member_id, "status": "pending"}


def approve_member(community_id: str, member_id: str, db_path: str = "pox.db") -> dict:
    with _connect(db_path) as con:
        con.execute(
            "UPDATE community_members SET status='active' WHERE community_id=? AND member_id=?",
            (community_id, member_id),
        )
    return {"community_id": community_id, "member_id": member_id, "status": "active"}


def get_members(community_id: str, db_path: str = "pox.db") -> list:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT member_id, status, joined_at FROM community_members WHERE community_id=? AND status='active'",
            (community_id,),
        ).fetchall()
    return [{"member_id": r[0], "status": r[1], "joined_at": r[2]} for r in rows]


def is_member(community_id: str, user_id: str, db_path: str = "pox.db") -> bool:
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT 1 FROM community_members WHERE community_id=? AND member_id=? AND status='active'",
            (community_id, user_id),
        ).fetchone()
    return row is not None


def is_founder(community_id: str, user_id: str, db_path: str = "pox.db") -> bool:
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT 1 FROM communities WHERE id=? AND founder=?", (community_id, user_id)
        ).fetchone()
    return row is not None


def get_my_communities(user_id: str, db_path: str = "pox.db") -> list:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT c.id, c.name, c.description, c.founder, c.created_at, cm.status "
            "FROM communities c JOIN community_members cm ON c.id=cm.community_id "
            "WHERE cm.member_id=?",
            (user_id,),
        ).fetchall()
    return [
        {"id": r[0], "name": r[1], "description": r[2], "founder": r[3], "created_at": r[4], "status": r[5]}
        for r in rows
    ]


def get_pending_requests(community_id: str, db_path: str = "pox.db") -> list:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT member_id, joined_at FROM community_members WHERE community_id=? AND status='pending'",
            (community_id,),
        ).fetchall()
    return [{"member_id": r[0], "joined_at": r[1]} for r in rows]


def update_community(community_id: str, name: str, description: str, requester_id: str, db_path: str = "pox.db") -> dict | None:
    """Founder only. Returns updated community or None if not found / not authorized."""
    with _connect(db_path) as con:
        row = con.execute("SELECT founder FROM communities WHERE id=?", (community_id,)).fetchone()
        if row is None or row[0] != requester_id:
            return None
        con.execute(
            "UPDATE communities SET name=?, description=? WHERE id=?",
            (name.strip(), (description or "").strip(), community_id),
        )
    return get_community(community_id, db_path)
