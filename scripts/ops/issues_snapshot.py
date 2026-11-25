"""Issue の写し（指示書82 §3-4）。GitHub の REST API から、Issue の表（§3-1）と開いている PR の表（§3-2）を作る。

- 書くのは、番号・題・状態・ラベルの名前・日付・PR 番号・ブランチ名だけ。Issue と PR の本文・コメントは写さない
  （本文は「つながる PR」の番号を拾うためだけに読み、書き出さない）
- つながる PR：Issue の本文に「#番号」で書かれた PR（「決定 #7」のような決定の番号は除く）
- トークンは環境変数 GITHUB_TOKEN から読む（無ければ鍵なしで読む）。表示もファイルへの書き出しもしない
- 開いている Issue の見立て（§3-3）は作らない（人が書く）
- 書き出すファイルは日付ごとの新しいファイル。同じ名前のファイルがあれば上書きしない（古い写しは残す）

使い方:
  python scripts/ops/issues_snapshot.py --repo PoX-Praxis/matching-program --out docs/reports/issues_snapshot_2026-11-25.md
  python scripts/ops/issues_snapshot.py --from-json data.json --out …   （取ってきた形の JSON から描くだけ）
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.github.com"
JST = timezone(timedelta(hours=9))
MENTION = re.compile(r"(?<!決定)(?<!決定 )#([0-9]+)\b")


def _get(url, token=None):
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pox-issues-snapshot"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
        return json.load(r)


def _pages(path, get, per_page=100):
    out, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        got = get(f"{API}{path}{sep}per_page={per_page}&page={page}")
        out += got
        if len(got) < per_page:
            return out
        page += 1


def fetch(repo, get):
    """repo の Issue（開いている・閉じたもの）と PR の番号、開いている PR を取る。get(url) → JSON。
    返り値は描くのに要る欄だけ（本文は、つながる PR の番号にしてから捨てる）。"""
    items = _pages(f"/repos/{repo}/issues?state=all", get)
    pr_numbers = sorted(i["number"] for i in items if "pull_request" in i)
    issues = [{"number": i["number"], "title": i["title"], "state": i["state"],
               "labels": [l["name"] if isinstance(l, dict) else str(l) for l in i.get("labels") or []],
               "created_at": i.get("created_at"), "closed_at": i.get("closed_at"),
               "linked_prs": linked_prs(i.get("body"), pr_numbers)}
              for i in items if "pull_request" not in i]
    pulls = _pages(f"/repos/{repo}/pulls?state=open", get)
    open_prs = [{"number": p["number"], "title": p["title"], "branch": (p.get("head") or {}).get("ref"),
                 "created_at": p.get("created_at"), "draft": bool(p.get("draft"))} for p in pulls]
    return {"issues": issues, "open_prs": open_prs}


def linked_prs(body, pr_numbers):
    """本文に「#番号」で書かれた PR の番号（決定の番号・Issue の番号は除く）。本文は返さない。"""
    prs = set(pr_numbers)
    return sorted({int(n) for n in MENTION.findall(str(body or "")) if int(n) in prs})


def _date(ts):
    if not ts:
        return ""
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(JST).strftime("%Y-%m-%d")


def _cell(t):
    return str(t).replace("|", "｜").replace("\n", " ").strip()


def render(data, now=None, repo=""):
    """§3-1（Issue の表）と §3-2（開いている PR の表）の Markdown。"""
    now = (now or datetime.now(JST)).astimezone(JST)
    issues = sorted(data["issues"], key=lambda i: -i["number"])
    n_open = sum(1 for i in issues if i["state"].lower() == "open")
    lines = [f"# Issue の写し（{now.strftime('%Y-%m-%d')}）", "",
             f"- リポジトリ：{repo}" if repo else None,
             f"- Issue の合計：{len(issues)}（開いている {n_open}・閉じた {len(issues) - n_open}）",
             f"- 写した日時：{now.strftime('%Y-%m-%d %H:%M')} JST",
             "- 本文とコメントは写していない。つながる PR は、Issue の本文に「#番号」で書かれた PR（決定の番号は除く）", "",
             "## Issue（番号の大きい順）", "",
             "| 番号 | 題 | 状態 | ラベル | 作った日 | 閉じた日 | つながる PR |",
             "|---|---|---|---|---|---|---|"]
    for i in issues:
        lines.append(f"| #{i['number']} | {_cell(i['title'])} | {i['state'].lower()} | {_cell('・'.join(i['labels']))} | "
                     f"{_date(i.get('created_at'))} | {_date(i.get('closed_at'))} | {' '.join(f'#{n}' for n in i['linked_prs'])} |")
    prs = sorted(data["open_prs"], key=lambda p: -p["number"])
    lines += ["", f"## 開いている PR（{len(prs)}）", "", "| 番号 | 題 | ブランチ | 作った日 | 下書き |", "|---|---|---|---|---|"]
    for p in prs:
        lines.append(f"| #{p['number']} | {_cell(p['title'])} | `{_cell(p['branch'] or '')}` | {_date(p.get('created_at'))} | "
                     f"{'はい' if p['draft'] else 'いいえ'} |")
    return "\n".join(x for x in lines if x is not None) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Issue の写しを作る（指示書82 §3-4）")
    ap.add_argument("--repo", default="PoX-Praxis/matching-program")
    ap.add_argument("--out", required=True)
    ap.add_argument("--from-json", help="取ってきた形（{issues, open_prs}）の JSON から描く")
    a = ap.parse_args(argv)
    if os.path.exists(a.out):
        sys.exit(f"{a.out} はもうあります（古い写しは残す。日付を変えた新しいファイルにする）")
    if a.from_json:
        data = json.load(open(a.from_json, encoding="utf-8"))
    else:
        token = os.environ.get("GITHUB_TOKEN") or None
        data = fetch(a.repo, lambda url: _get(url, token))
    md = render(data, repo=a.repo)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"Issue {len(data['issues'])}・開いている PR {len(data['open_prs'])} → {a.out}")


if __name__ == "__main__":
    main()
