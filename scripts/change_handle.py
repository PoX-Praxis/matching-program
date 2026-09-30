#!/usr/bin/env python3
"""ハンドルの例外の変更（指示書55-2 PR-D・運用）。本人の申立てがあったときだけ運用者が実行する。

規則: 一意性を保つ／1 回だけ／旧ハンドルは退役させて再利用しない／変更は handle_changes に記録
（画面には出さない）。公開の API は作らない。

使い方: python scripts/change_handle.py <subject_id> <new_handle> --note "申立ての要旨" [--db <path>]
"""
import argparse, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import handles  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("subject_id")
    ap.add_argument("new_handle")
    ap.add_argument("--note", default="")
    ap.add_argument("--db", default=os.environ.get("POX_DB", "pox.db"))
    a = ap.parse_args()
    try:
        h = handles.change_handle_by_exception(a.subject_id, a.new_handle, note=a.note, db_path=a.db)
    except ValueError as e:
        print(f"変更できません: {e}")
        return 1
    print(f"@{h} に変更しました（旧ハンドルは退役・再利用されません）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
