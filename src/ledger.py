#!/usr/bin/env python3
"""
PoX 台帳層 — connection_ledger.schema.json v1.0 に準拠した承認の記録。

approve(A, B) ... A が B を承認する（1方向）
approve(B, A) ... B が A を承認する → 相互成立 → established_at が立つ

vessel_id はソート正規化（どちらが先に呼んでも同じ vessel を指す）。

接続: get_connection(db_path) を使用。SQL は %s プレースホルダ統一。
"""
import json, uuid
from datetime import datetime, timezone
from db_connect import get_connection, is_postgres


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS vessels "
            "(vessel_id TEXT PRIMARY KEY, vessel_json TEXT NOT NULL)"
        )
        con.commit()
    return con


def approve(
    from_id: str,
    to_id: str,
    match_run_id: str = None,
    predicted_role: str = None,
    phase: str = None,
    db_path: str = "pox.db",
) -> dict:
    """
    from_id が to_id を承認する。
    vessel_id = 'v_' + '_'.join(sorted([from_id, to_id])) で正規化。
    双方向の承認が揃ったら established_at / is_connected を更新する。
    戻り値: {"vessel_id", "established": bool}
    """
    vessel_id = "v_" + "_".join(sorted([from_id, to_id]))

    with _connect(db_path) as con:
        row = con.execute(
            "SELECT vessel_json FROM vessels WHERE vessel_id = %s", (vessel_id,)
        ).fetchone()

        if row:
            vessel = json.loads(row[0])
        else:
            vessel = {
                "schema_version": "1.0",
                "vessel_id": vessel_id,
                "created_at": _now(),
                "founder": from_id,
                "phase_at_creation": phase,
                "membership_count": 1,
                "is_connected": False,
                "joins": [
                    {
                        "join_id": f"j_{uuid.uuid4().hex[:8]}",
                        "joiner": to_id,
                        "candidate_source": {
                            "match_run_id": match_run_id,
                            "channel": "complementary",
                            "predicted_role": predicted_role,
                        } if (match_run_id or predicted_role) else None,
                        "opened_at": _now(),
                        "approvals": [],
                        "established_at": None,
                        "terminal_state": "active",
                        "closed_at": None,
                        "contributions": [{"actor": from_id, "role": "founded"}],
                    }
                ],
            }

        join = vessel["joins"][0]
        founder = vessel["founder"]
        joiner  = join["joiner"]

        approvers = {a["from"] for a in join["approvals"]}
        if from_id not in approvers:
            to_field = joiner if from_id == founder else vessel_id
            join["approvals"].append({"from": from_id, "to": to_field, "at": _now()})

        approvers = {a["from"] for a in join["approvals"]}
        if founder in approvers and joiner in approvers and join["established_at"] is None:
            join["established_at"] = _now()
            join["closed_at"]      = _now()
            vessel["is_connected"]    = True
            vessel["membership_count"] = 2
            if not any(c["actor"] == joiner for c in join["contributions"]):
                join["contributions"].append({"actor": joiner, "role": "approved"})

        con.execute(
            "INSERT INTO vessels (vessel_id, vessel_json) VALUES (%s, %s) "
            "ON CONFLICT (vessel_id) DO UPDATE SET vessel_json = EXCLUDED.vessel_json",
            (vessel_id, json.dumps(vessel, ensure_ascii=False)),
        )

    return {"vessel_id": vessel_id, "established": vessel["is_connected"]}


def load_all_vessels(db_path: str = "pox.db") -> list[dict]:
    """全 vessel を返す（vessel_id 順）。"""
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT vessel_json FROM vessels ORDER BY vessel_id"
        ).fetchall()
    return [json.loads(row[0]) for row in rows]
