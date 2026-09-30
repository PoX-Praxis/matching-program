#!/usr/bin/env python3
"""埋め込みとアカウントの棚卸し（指示書55 段階0-2 E-1〜E-3・B-2・B-5）。読み取りのみ（DB に書かない）。

HTTP からも同じ処理を実行できる: GET /ledger/audit/inventory（X-Anchor-Token ヘッダ必須）。

返すもの（判定書 §5-1 の条件: 読み取りのみ・スコアを返さない・個人の履歴を横断して並べない）
  embedding    … 実行中の BACKEND / MODEL_TAG / FULL_DIM と、profile_vectors の列の宣言次元
  vectors      … profile_vectors の (model_tag, is_active) ごとの本数
  profiles_v4  … 総数・generation_status ごとの件数・現行 model_tag のベクトルが無い人数
  accounts     … id ごとの所在（auth_identities / profiles_v4 / 旧 seekers）・表示名・
                 ベクトルの model_tag・台帳参照の件数（削除か非表示かの判断材料）
  ledger       … connection.closed の件数（payload 互換の確認）と ledger_v4 の event ごとの件数
本文・スコア・照合の結果は返さない。

使い方: python scripts/audit_inventory.py [--db <path>]
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from db_connect import get_connection, is_postgres  # noqa: E402
import embedding_config as EC  # noqa: E402


def _rows(sql, params=(), *, db_path):
    """1 クエリ 1 接続（Postgres で失敗した文がトランザクションを汚さないように）。表が無ければ None。"""
    try:
        with get_connection(db_path) as con:
            return [tuple(r) if not isinstance(r, dict) else tuple(r.values())
                    for r in con.execute(sql, params).fetchall()]
    except Exception:  # noqa: BLE001 — 表が無い環境（SQLite の v4 等）は「無い」と返す
        return None


def _column_dim(db_path):
    if not is_postgres():
        return None
    r = _rows("SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
              "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
              "WHERE n.nspname='public' AND c.relname='profile_vectors' "
              "AND a.attname='will_symmetric' AND NOT a.attisdropped", db_path=db_path)
    if not r:
        return None
    import re
    m = re.search(r"vector\((\d+)\)", str(r[0][0]))
    return int(m.group(1)) if m else None


def run_inventory(*, db_path="pox.db") -> dict:
    """棚卸しの本体（CLI と GET /ledger/audit/inventory の共通）。読み取りのみ。"""
    out = {"embedding": {
        "backend": EC.BACKEND,
        "backend_env_set": "POX_EMBED_BACKEND" in os.environ,
        "model_tag": EC.MODEL_TAG,
        "model_tag_env_set": "POX_EMBED_MODEL_TAG" in os.environ,
        "full_dim": EC.FULL_DIM,
        "column_dim": _column_dim(db_path),
        "endpoint_set": {"qwen3": bool(EC.QWEN3_ENDPOINT), "embgemma": bool(EC.EMBGEMMA_ENDPOINT),
                         "nomic": bool(EC.NOMIC_ENDPOINT)},
    }}

    vec = _rows("SELECT model_tag, is_active, count(*) FROM profile_vectors "
                "GROUP BY model_tag, is_active ORDER BY model_tag, is_active", db_path=db_path)
    out["vectors"] = None if vec is None else [
        {"model_tag": t, "is_active": bool(a), "count": int(n)} for t, a, n in vec]

    pv4 = _rows("SELECT id, generation_status, created_at FROM profiles_v4", db_path=db_path)
    tags = {}
    for pid, t, a in (_rows("SELECT profile_id, model_tag, is_active FROM profile_vectors",
                            db_path=db_path) or []):
        tags.setdefault(pid, []).append(t if a else f"{t}(inactive)")
    if pv4 is None:
        out["profiles_v4"] = None
    else:
        by_status = {}
        for _, st, _ in pv4:
            by_status[st] = by_status.get(st, 0) + 1
        out["profiles_v4"] = {
            "total": len(pv4), "by_generation_status": by_status,
            "without_current_model_tag": sum(1 for pid, _, _ in pv4 if EC.MODEL_TAG not in tags.get(pid, [])),
        }

    auth = {r[0]: r[1] for r in (_rows("SELECT subject_id, created_at FROM auth_identities",
                                       db_path=db_path) or [])}
    v3 = {r[0] for r in (_rows("SELECT id FROM seekers", db_path=db_path) or [])}
    names = {r[0]: r[1] for r in (_rows("SELECT subject_id, name FROM display_names",
                                        db_path=db_path) or [])}
    v4 = {pid: (st, created) for pid, st, created in (pv4 or [])}
    ledger = _rows("SELECT actor, type, payload_json FROM ledger_events", db_path=db_path) or []

    ids = sorted(set(auth) | v3 | set(v4))
    accounts = []
    for i in ids:
        refs = sum(1 for actor, _, payload in ledger if actor == i or f'"{i}"' in (payload or ""))
        accounts.append({
            "id": i,
            "display_name": names.get(i),
            "in_auth": i in auth,
            "in_profiles_v4": i in v4,
            "in_seekers_v3": i in v3,
            "generation_status": v4[i][0] if i in v4 else None,
            "registered_at": str(auth.get(i) or (v4[i][1] if i in v4 else "") or "") or None,
            "vector_model_tags": sorted(tags.get(i, [])),
            "ledger_refs": refs,
        })
    out["accounts"] = accounts
    out["accounts_without_auth"] = [a["id"] for a in accounts if not a["in_auth"]]

    lv4 = _rows("SELECT event, count(*) FROM ledger_v4 GROUP BY event ORDER BY event", db_path=db_path)
    out["ledger"] = {
        "connection_closed_count": sum(1 for _, t, _ in ledger if t == "connection.closed"),
        "connection_established_count": sum(1 for _, t, _ in ledger if t == "connection.established"),
        "ledger_v4_by_event": None if lv4 is None else {e: int(n) for e, n in lv4},
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("POX_DB", "pox.db"))
    a = ap.parse_args()
    print(json.dumps(run_inventory(db_path=a.db), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
