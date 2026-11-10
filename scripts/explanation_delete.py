#!/usr/bin/env python3
"""指示書69 §4-2 (5) — 退会・削除の求めのとき、その人が関わる接続の説明の本文を消す（運営が実行する）。

- 消すもの（本体の DB）: 申請・承認の表示（記号のままの HTML と、見た人ごとの HTML）・カードの JSON・補足・説明の向きのレコード
- 残すもの: 表示の行（ハッシュ・時刻・向きと「削除済み」の印）・台帳の explanation.agreed
- 合意ごとに redaction.recorded（hash_kind "explanation"）を台帳に書く
- **既定は数えるだけ**（--apply を付けたときだけ消す）。実行の前に Postgres のダンプを取ること（docs/account_purge.md と同じ）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import explanation_agreement as XA  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--subject", required=True, help="削除を求めた人の subject_id")
    ap.add_argument("--reason", default="subject_request", choices=("subject_request", "legal"))
    ap.add_argument("--decided-by", default="ops")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=os.environ.get("POX_DB", "pox.db"))
    args = ap.parse_args(argv)
    res = XA.delete_for_subject(args.subject, reason_class=args.reason, decided_by=args.decided_by,
                                apply=args.apply, db_path=args.db)
    print(json.dumps(res, ensure_ascii=False))
    return res


if __name__ == "__main__":
    main()
