#!/usr/bin/env python3
"""
PoX ハンドル（指示書55 §0-9・55-2 PR-D）。表示は「表示名（@ハンドル）」。

- **一意・不変**。1 つの subject に 1 つ。いったん決めたら本人は変えられない。
- 書式は英小文字・数字・_ の 3〜30 字（大文字は小文字にそろえて保存。@ は付けない）。
- **他の subject_id と同じ文字列は使えない**（生 id とハンドルを取り違えないため）。
- **例外の変更**（運用の規則）: 本人の申立てで、一意性を保ったまま **1 回だけ**。旧ハンドルは
  **再利用しない**（retired_handles に残す）。変更は通常DB（handle_changes）に記録し、表示しない。
  運用者が scripts/change_handle.py で行う（公開の API は作らない）。
- 台帳・content_hash には入れない（表示名と同じく通常DBの値。同一性の根拠は subject_id）。
"""
import re
from datetime import datetime, timezone

from db_connect import get_connection, is_postgres

HANDLE_RE = re.compile(r"^[a-z0-9_]{3,30}$")
# 画面の経路や運用の語と紛れるもの（/u/<handle> の行き先を奪わせない）
RESERVED = frozenset({
    "admin", "api", "u", "me", "mypage", "login", "logout", "about", "connect", "profile",
    "pox", "root", "system", "support", "null", "none", "undefined", "anonymous",
})


class HandleError(ValueError):
    """書式・予約語の違反（400）。"""


class HandleTaken(ValueError):
    """既に使われている・再利用できない（409）。"""


class HandleImmutable(ValueError):
    """既に設定済みで変えられない（409）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        for ddl in SQLITE_DDL:
            con.execute(ddl)
        con.commit()
    return con


SQLITE_DDL = [
    "CREATE TABLE IF NOT EXISTS handles ("
    "subject_id TEXT PRIMARY KEY, handle TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS retired_handles ("
    "handle TEXT PRIMARY KEY, subject_id TEXT NOT NULL, retired_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS handle_changes ("
    "subject_id TEXT NOT NULL, old_handle TEXT NOT NULL, new_handle TEXT NOT NULL, "
    "changed_at TEXT NOT NULL, note TEXT)",
]


def normalize(handle: str) -> str:
    """前後の空白と先頭の @ を外し、小文字にそろえる。書式に合わなければ HandleError。"""
    h = (handle or "").strip()
    if h.startswith("@"):
        h = h[1:]
    h = h.lower()
    if not HANDLE_RE.match(h):
        raise HandleError("ハンドルは英小文字・数字・_ の 3〜30 字です")
    if h in RESERVED:
        raise HandleError("このハンドルは使えません")
    return h


def _is_subject_id_of_other(con, h, subject_id) -> bool:
    """h が他の subject の id と同じか（生 id とハンドルの取り違えを防ぐ）。"""
    for sql in ("SELECT 1 FROM auth_identities WHERE subject_id=%s",
                "SELECT 1 FROM profiles_v4 WHERE id=%s",
                "SELECT 1 FROM seekers WHERE id=%s"):
        try:
            if con.execute(sql, (h,)).fetchone() and h != subject_id:
                return True
        except Exception:  # noqa: BLE001 — 表が無い環境
            if is_postgres():
                con.rollback()
    return False


def _available(con, h, subject_id) -> bool:
    if con.execute("SELECT 1 FROM handles WHERE handle=%s", (h,)).fetchone():
        return False
    if con.execute("SELECT 1 FROM retired_handles WHERE handle=%s", (h,)).fetchone():
        return False                                  # 旧ハンドルは再利用しない
    return not _is_subject_id_of_other(con, h, subject_id)


def check(handle: str, subject_id: str = None, db_path: str = "pox.db") -> str:
    """設定できるかを確かめて正規化した値を返す（書き込まない）。"""
    h = normalize(handle)
    with _connect(db_path) as con:
        if not _available(con, h, subject_id):
            raise HandleTaken("このハンドルは既に使われています")
    return h


def set_handle(subject_id: str, handle: str, db_path: str = "pox.db") -> str:
    """初回の設定。既に持っていれば HandleImmutable（不変）。"""
    h = normalize(handle)
    with _connect(db_path) as con:
        if con.execute("SELECT 1 FROM handles WHERE subject_id=%s", (subject_id,)).fetchone():
            raise HandleImmutable("ハンドルは設定済みです（後から変えられません）")
        if not _available(con, h, subject_id):
            raise HandleTaken("このハンドルは既に使われています")
        con.execute("INSERT INTO handles (subject_id, handle, created_at) VALUES (%s, %s, %s)",
                    (subject_id, h, _now()))
    return h


def change_handle_by_exception(subject_id: str, new_handle: str, note: str = "",
                               db_path: str = "pox.db") -> str:
    """例外の変更（運用）。**1 回だけ**・一意性を保つ・旧ハンドルは退役させて再利用しない。"""
    h = normalize(new_handle)
    with _connect(db_path) as con:
        row = con.execute("SELECT handle FROM handles WHERE subject_id=%s", (subject_id,)).fetchone()
        if not row:
            raise HandleError("ハンドルが未設定です（初回の設定は本人が行う）")
        old = row[0]
        if con.execute("SELECT 1 FROM handle_changes WHERE subject_id=%s", (subject_id,)).fetchone():
            raise HandleImmutable("例外の変更は 1 回までです")
        if h == old or not _available(con, h, subject_id):
            raise HandleTaken("このハンドルは使えません")
        now = _now()
        con.execute("INSERT INTO retired_handles (handle, subject_id, retired_at) VALUES (%s, %s, %s)",
                    (old, subject_id, now))
        con.execute("UPDATE handles SET handle=%s WHERE subject_id=%s", (h, subject_id))
        con.execute("INSERT INTO handle_changes (subject_id, old_handle, new_handle, changed_at, note) "
                    "VALUES (%s, %s, %s, %s, %s)", (subject_id, old, h, now, note or ""))
    return h


def get_handle(subject_id: str, db_path: str = "pox.db"):
    with _connect(db_path) as con:
        row = con.execute("SELECT handle FROM handles WHERE subject_id=%s", (subject_id,)).fetchone()
    return row[0] if row else None


def get_many(subject_ids, db_path: str = "pox.db") -> dict:
    ids = [s for s in {s for s in subject_ids if s}]
    if not ids:
        return {}
    ph = ",".join(["%s"] * len(ids))
    with _connect(db_path) as con:
        rows = con.execute(f"SELECT subject_id, handle FROM handles WHERE subject_id IN ({ph})",
                           tuple(ids)).fetchall()
    return {r[0]: r[1] for r in rows}


def subject_for(handle: str, db_path: str = "pox.db"):
    """ハンドル → subject_id（無ければ None）。退役したハンドルは誰にも解決しない。"""
    try:
        h = normalize(handle)
    except HandleError:
        return None
    with _connect(db_path) as con:
        row = con.execute("SELECT subject_id FROM handles WHERE handle=%s", (h,)).fetchone()
    return row[0] if row else None
