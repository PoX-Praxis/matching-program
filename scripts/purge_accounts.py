#!/usr/bin/env python3
"""アカウント整理（指示書55-3 §2。発注者の明示の許可のもとで作る一回限りの削除）。

**対象は下の 4 id にハードコードで固定**（汎用の削除 API にしない。任意の id は消せない）。
2026-09-30 の inventory で「本人と紐づいていない（auth_identities に行が無い）・台帳参照 0」を確認した
テスト用／二重登録の id。

- **既定は dry-run**（消える行数を数えて返すだけ）。apply=True のときだけ削除する
- 実行時に対象ごとに **台帳参照（ledger_refs）を再確認**し、0 でなければその id は**消さない**
  （参照があるものは物理削除しない規則。非表示の扱いに回す）。本人と紐づいた id・メンバー記録がある id も同じ
- 削除は **1 トランザクション**（途中で失敗したら全部元のまま）
- 台帳（ledger_events）には触らない（参照 0 を確かめてから消すので、台帳が指す先は壊れない）
- ledger_v4 の match_ranked（照合の内部記録）は profiles_v4 を外部キーで参照するため、対象 id の行を
  併せて消す（未認証で照合を叩けた期間の記録で、意思決定に使わないもの。判定書 §11-3）
- **実行前に Render の Postgres のダンプ（pg_dump）を手元に取ること**（docs/account_purge.md）

HTTP のルート（POST /ledger/admin/purge-accounts）は使用後に閉じた（指示書58 §2-4）。いまは CLI だけ。
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from db_connect import get_connection  # noqa: E402

PURGE_IDS = ("kaoru", "smoke_test", "u_5672a380", "u_9fa00efa")

# (表, id の列)。外部キーの向きに合わせて子 → 親の順。表が無い環境では飛ばす。
_TARGETS = (
    ("ledger_v4", "seeker_id"), ("ledger_v4", "candidate_id"),
    ("profile_vectors", "profile_id"), ("derived_necessity", "profile_id"),
    ("profiles_v4", "id"),
    ("seeker_embeddings", "seeker_id"), ("seekers", "id"), ("profiles", "user_id"),
    ("user_snapshots", "user_id"), ("snapshot_visibility_log", "user_id"),
    ("policy_consents", "user_id"), ("display_names", "subject_id"), ("handles", "subject_id"),
    ("declaration_drafts", "subject_id"),
    ("connection_requests", "from_subject"), ("connection_requests", "to_subject"),
    ("messages", "from_id"), ("messages", "to_id"),
)


def _count(sql, params, db_path):
    try:
        with get_connection(db_path) as con:
            r = con.execute(sql, params).fetchone()
        return int(r[0])
    except Exception:  # noqa: BLE001 — 表が無い環境
        return None


def _ledger_refs(i, db_path):
    try:
        with get_connection(db_path) as con:
            rows = con.execute("SELECT actor, payload_json FROM ledger_events").fetchall()
    except Exception:  # noqa: BLE001
        return 0
    return sum(1 for actor, payload in rows if actor == i or f'"{i}"' in (payload or ""))


def _blocker(i, db_path):
    """消してはいけない理由（無ければ None）。"""
    refs = _ledger_refs(i, db_path)
    if refs:
        return f"台帳の参照がある（{refs} 件）。物理削除せず非表示の扱いにする"
    if _count("SELECT count(*) FROM auth_identities WHERE subject_id=%s", (i,), db_path):
        return "本人と紐づいている（auth_identities に行がある）"
    if _count("SELECT count(*) FROM community_members WHERE member_id=%s", (i,), db_path):
        return "コミュニティのメンバー記録がある"
    return None


def _plan(i, db_path):
    rows = {}
    for table, col in _TARGETS:
        n = _count(f"SELECT count(*) FROM {table} WHERE {col}=%s", (i,), db_path)
        if n:
            rows[f"{table}.{col}"] = n
    n = _count("SELECT count(*) FROM necessities WHERE owner_ref=%s", (i,), db_path)
    if n:
        rows["necessities.owner_ref"] = n
    return rows


def run_purge(*, apply=False, db_path="pox.db") -> dict:
    accounts = []
    for i in PURGE_IDS:
        accounts.append({"id": i, "rows": _plan(i, db_path), "blocked": _blocker(i, db_path)})
    targets = [a for a in accounts if not a["blocked"] and a["rows"]]
    if apply and targets:
        with get_connection(db_path) as con:          # 全対象で 1 トランザクション
            for a in targets:
                i = a["id"]
                if "necessities.owner_ref" in a["rows"]:
                    con.execute("DELETE FROM necessity_evidence WHERE necessity_id IN "
                                "(SELECT necessity_id FROM necessities WHERE owner_ref=%s)", (i,))
                    con.execute("DELETE FROM necessities WHERE owner_ref=%s", (i,))
                for key in a["rows"]:
                    table, col = key.split(".")
                    if table != "necessities":
                        con.execute(f"DELETE FROM {table} WHERE {col}=%s", (i,))
    for a in accounts:
        a["deleted"] = bool(apply and a in targets)
    return {
        "apply": bool(apply),
        "accounts": accounts,
        "deleted_ids": [a["id"] for a in accounts if a["deleted"]],
        "profiles_v4_remaining": _count("SELECT count(*) FROM profiles_v4", (), db_path),
        "seekers_v3_remaining": _count("SELECT count(*) FROM seekers", (), db_path),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("POX_DB", "pox.db"))
    ap.add_argument("--apply", action="store_true", help="実際に削除する（既定は確認だけ）")
    a = ap.parse_args()
    print(json.dumps(run_purge(apply=a.apply, db_path=a.db), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
