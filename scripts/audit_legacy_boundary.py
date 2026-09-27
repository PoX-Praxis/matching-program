#!/usr/bin/env python3
"""削除の検証の legacy 境界を決める監査（指示書45C §1-2）。読み取りのみ（台帳・DB に書かない）。

使い方（本番。DATABASE_URL が設定された環境で）:
  python scripts/audit_legacy_boundary.py                         # 段 1: #101 の時刻を境界候補にする
  python scripts/audit_legacy_boundary.py --boundary-at <#113 のデプロイ完了時刻 ISO8601Z>   # 段 2

  段 1 で不一致が 0 件 → 出力の boundary_seq を POX_LEGACY_BOUNDARY_SEQ に設定する。
  1 件以上              → #113 のデプロイ完了時刻で段 2 を実行し、不一致 0 件を確認してから設定する。
ローカルの SQLite を監査する場合は --db <path>。
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import redaction  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--boundary-at", default=redaction.BOUNDARY_CANDIDATE_101_AT,
                    help="境界候補の時刻（UTC・ISO8601。既定は #101 の main 反映時刻）")
    ap.add_argument("--boundary-seq", type=int, default=None, help="時刻の代わりに seq で与える")
    ap.add_argument("--db", default=os.environ.get("POX_DB", "pox.db"))
    a = ap.parse_args()
    seq = a.boundary_seq if a.boundary_seq is not None else redaction.seq_before(a.boundary_at, db_path=a.db)
    r = redaction.audit_after_boundary(seq, db_path=a.db)
    r["boundary_at"] = None if a.boundary_seq is not None else a.boundary_at
    r["verdict"] = ("境界として採用できます（不一致 0 件）" if not r["mismatches"]
                    else "不一致があります。#113 のデプロイ完了時刻で再実行してください")
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if not r["mismatches"] else 1


if __name__ == "__main__":
    sys.exit(main())
