#!/usr/bin/env python3
"""手作業検証の道具（指示書57 §3）。**開発用・本番には入れない**（ルートを持たない）。

2 人の ①v5 の出力（JSON ファイル）を受け取り、片方の目的ごとの必要像の文 × もう片方の与え像の文の
**全対**を並べて CSV に書く。人が「対応する／しない」を付け、システムの判定（judge）と突き合わせる。
一致率は内部の検証にだけ使い、指標として公開しない。

使い方:
  POX_EMBED_BACKEND=nomic POX_EMBED_MODEL_TAG=nomic-emb-v2 POX_NOMIC_ENDPOINT=... \\
    python scripts/manual_pairs.py a.json b.json > pairs.csv
  （backend を指定しなければ stub＝意味の無い値になる。形の確認だけに使う）
列: 向き, 目的, 必要像の文, 必須, 与え像の文, システムの判定, 人の判定（空欄＝人が埋める）
"""
import csv, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _embed(text, role):
    from embedding_service import embed
    return embed(text, role)[0]


def _rows(a, b, label):
    from matcher_v5 import judge
    offers = [(o["文"], _embed(o["文"], "passage")) for o in (b.get("与え像") or [])]
    for p in a.get("purposes") or []:
        for n in p.get("必要像") or []:
            nv = _embed(n["文"], "query")
            for ot, ov in offers:
                yield [label, p.get("向かう先", ""), n["文"], "必須" if n.get("必須") else "歓迎", ot,
                       "対応" if judge(nv, ov) else "—", ""]


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    a, b = (json.load(open(f, encoding="utf-8")) for f in sys.argv[1:3])
    w = csv.writer(sys.stdout)
    w.writerow(["向き", "目的", "必要像の文", "必須", "与え像の文", "システムの判定", "人の判定"])
    for r in list(_rows(a, b, "A の必要像 × B の与え像")) + list(_rows(b, a, "B の必要像 × A の与え像")):
        w.writerow(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
