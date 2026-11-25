"""指示書82 §3-4 — Issue の写しを作る道具（偽の API の応答で表が作れること）。テスト 582〜584。

582 偽の API（2 頁・Issue と PR が混じる）から、Issue の表と開いている PR の表を作る。並び・数・日付（JST）・つながる PR
583 本文・トークンは書き出さない。決定の番号（「決定 #7」）と Issue の番号は、つながる PR にしない
584 同じ名前のファイルがあれば上書きしない（古い写しは残す）
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "ops"))
import issues_snapshot as IS  # noqa: E402

SECRET_BODY = "本文の秘密の文（写さない）"


def _issue(n, title, state="open", labels=(), body="", created="2026-11-12T08:15:17Z", closed=None):
    return {"number": n, "title": title, "state": state, "labels": [{"name": l} for l in labels], "body": body,
            "created_at": created, "closed_at": closed}


def _pr_item(n):
    return {"number": n, "title": f"PR {n}", "state": "closed", "labels": [], "body": SECRET_BODY, "pull_request": {"url": "x"},
            "created_at": "2026-11-01T00:00:00Z"}


ITEMS = ([_issue(10, "題 A", labels=("後で決める",), body=f"{SECRET_BODY} → #11（済み）。決定 #7・決定#8 と #12（Issue）"),
          _issue(12, "題 B｜縦棒", state="closed", body="#9", created="2026-11-12T20:00:00Z", closed="2026-11-20T16:30:00Z")]
         + [_pr_item(n) for n in (7, 8, 9, 11)] + [_issue(n, f"埋め {n}") for n in range(100, 198)])
PULLS = [{"number": 11, "title": "開いた PR", "head": {"ref": "claude/x"}, "created_at": "2026-11-24T23:00:00Z", "draft": True,
          "body": SECRET_BODY}]


class FakeApi:
    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        page = int(url.rsplit("page=", 1)[1])
        if "/issues?" in url:
            return ITEMS[(page - 1) * 100:page * 100]
        return PULLS if page == 1 else []


def test_t582_tables_from_fake_api():
    api = FakeApi()
    data = IS.fetch("o/r", api)
    assert sum("/issues?" in u for u in api.urls) == 2 and any("state=all" in u for u in api.urls)   # 2 頁を読む
    assert len(data["issues"]) == 2 + 98 and [p["number"] for p in data["open_prs"]] == [11]
    md = IS.render(data, now=datetime(2026, 11, 25, 12, 0, tzinfo=timezone(timedelta(hours=9))), repo="o/r")
    assert "Issue の合計：100（開いている 99・閉じた 1）" in md and "2026-11-25 12:00 JST" in md
    rows = [l for l in md.splitlines() if l.startswith("| #")]
    nums = [int(r.split("|")[1].strip()[1:]) for r in rows[:-1]]
    assert nums == sorted(nums, reverse=True)                                  # 番号の大きい順
    a = next(r for r in rows if r.startswith("| #10 "))
    assert a == "| #10 | 題 A | open | 後で決める | 2026-11-12 |  | #11 |"
    b = next(r for r in rows if r.startswith("| #12 "))
    assert b == "| #12 | 題 B｜縦棒 | closed |  | 2026-11-13 | 2026-11-21 | #9 |"   # 日付は JST
    assert "| #11 | 開いた PR | `claude/x` | 2026-11-25 | はい |" in md


def test_t583_no_body_no_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "s" * 36)
    md = IS.render(IS.fetch("o/r", FakeApi()))
    assert SECRET_BODY not in md and "ghp_" not in md
    assert IS.linked_prs("決定 #7・決定#8・#12・#11・#999", [7, 8, 9, 11]) == [11]
    assert all("body" not in i for i in IS.fetch("o/r", FakeApi())["issues"])
    seen = []
    fake = FakeApi()
    monkeypatch.setattr(IS, "_get", lambda url, token=None: seen.append(token) or fake(url))
    monkeypatch.setattr(IS, "render", lambda data, now=None, repo="": "x\n")
    out = os.path.join(os.path.dirname(__file__), "..", "_t583_never_written.md")
    monkeypatch.setattr(IS.os.path, "exists", lambda p: False)
    monkeypatch.setattr("builtins.open", lambda *a, **k: __import__("io").StringIO())
    IS.main(["--out", out])
    assert seen and all(t == os.environ["GITHUB_TOKEN"] for t in seen)           # トークンは環境変数から渡るだけ


def test_t584_no_overwrite(tmp_path):
    src = tmp_path / "d.json"
    src.write_text(json.dumps(IS.fetch("o/r", FakeApi()), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "issues_snapshot_2026-11-25.md"
    IS.main(["--from-json", str(src), "--out", str(out), "--repo", "o/r"])
    first = out.read_text(encoding="utf-8")
    assert first.startswith("# Issue の写し（") and "| #10 |" in first
    with pytest.raises(SystemExit):
        IS.main(["--from-json", str(src), "--out", str(out)])
    assert out.read_text(encoding="utf-8") == first
