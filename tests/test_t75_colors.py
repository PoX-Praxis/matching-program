"""指示書75 — 色の名前の一本化（順1）とダークモード（順2）。テスト 489〜。

489（§7-1）テンプレートと、HTML を出すコードに、色の直書きが残っていない（_theme.html の色の定義を除く）
490（§7-2）色の名前がそろっている（参照している名前はすべて定義がある。全ページが _theme.html を読み込む）
491（§7-3）文字と地の組のコントラスト比が §3 の線を満たす（明るい表示。暗い表示は順2 で足す）
492（§7-6）説明の表示の HTML には色を書かない（クラスだけ。保存済みの見本と、新しく描いたもの）
"""
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import explanation as X  # noqa: E402

TPL = os.path.join(ROOT, "templates")
THEME = os.path.join(TPL, "_theme.html")
COLOR = re.compile(r"#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\([^)]*\)|hsla?\(")
NAMED = re.compile(r"(?<![\w-])(?:color|background(?:-color)?|border(?:-[a-z]+)?|outline|fill|stroke)\s*:\s*[^;\"'{}]*"
                   r"\b(?:white|black|navy|red|green|blue|gray|grey|orange|yellow|silver|purple)\b", re.I)


def _strip_non_css(s):
    """色と紛れる文字列（リンクの #・id の #・Jinja の注記）を除く。"""
    s = re.sub(r"\{#.*?#\}", "", s, flags=re.S)
    s = re.sub(r"(href|action)=\"#[^\"]*\"", "", s)
    s = re.sub(r"getElementById\([^)]*\)|querySelector(All)?\([^)]*\)|location\.hash", "", s)
    s = re.sub(r"['\"]#[A-Za-z][\w-]*['\"]", "", s)                    # "#notice" など（セレクタ）
    return s


def _defined_names():
    s = open(THEME, encoding="utf-8").read()
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", s))


# ── 489（§7-1）色の直書きが残っていない ───────────────────────────────────────────────
def test_t489_no_hardcoded_colors():
    left = []
    for path in sorted(glob.glob(os.path.join(TPL, "*.html"))):
        if path == THEME:
            continue
        s = _strip_non_css(open(path, encoding="utf-8").read())
        left += [(os.path.basename(path), m.group(0)) for m in COLOR.finditer(s)]
        left += [(os.path.basename(path), m.group(0)[:40]) for m in NAMED.finditer(s)]
    assert left == [], left
    # HTML を出すコード（src/・app.py）にも色を書かない
    for path in [os.path.join(ROOT, "app.py")] + sorted(glob.glob(os.path.join(ROOT, "src", "*.py"))):
        s = open(path, encoding="utf-8").read()
        for m in re.finditer(r"style=[\"'][^\"']*(?:color|background|border)[^\"']*[\"']", s):
            assert not COLOR.search(m.group(0)), (path, m.group(0))


# ── 490（§7-2）色の名前がそろっている ────────────────────────────────────────────────
def test_t490_color_names_defined_and_loaded_everywhere():
    names = _defined_names()
    colors = {k: v for k, v in names.items() if COLOR.search(v) or v.strip().startswith("#")}
    assert 24 <= len(colors) <= 32 and all(COLOR.search(v) for v in colors.values())
    other_defs = set()
    for path in glob.glob(os.path.join(TPL, "*.html")):
        other_defs |= set(re.findall(r"(--[a-z0-9-]+)\s*:", open(path, encoding="utf-8").read()))
    used = set()
    for path in glob.glob(os.path.join(TPL, "*.html")):
        used |= set(re.findall(r"var\((--[a-z0-9-]+)", open(path, encoding="utf-8").read()))
    assert used - other_defs == set(), used - other_defs                               # 定義の無い名前を使っていない
    for old in ("--pox-navy", "--pox-ink", "--pox-sub", "--pox-muted", "--pox-rule", "--pox-link", "--pox-bg-soft",
                "--ink", "--ink2", "--label", "--rule", "--navy", "--pivot", "--pivot-bg", "--need-bg", "--step-bg",
                "--step-ink", "--sub", "--accent-bg"):
        assert old not in other_defs and f"var({old})" not in json.dumps(sorted(used)), old   # ページごとの色の変数は寄せた
    for path in glob.glob(os.path.join(TPL, "*.html")):                                 # 全ページが色の定義を読み込む
        s = open(path, encoding="utf-8").read()
        if s.lstrip().startswith("<!DOCTYPE") or "<html" in s:
            assert '{% include "_theme.html" %}' in s, os.path.basename(path)
        elif "{% extends" in s:
            assert '{% extends "_base.html" %}' in s, os.path.basename(path)
    assert '{% include "_theme.html" %}' in open(os.path.join(TPL, "_base.html"), encoding="utf-8").read()


# ── 491（§7-3）コントラスト比（明るい表示）────────────────────────────────────────────
def _lum(h):
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4     # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# 文字と地の組（本文は 4.5 以上・大きな見出しと印は 3 以上）。地は、その文字が実際に載るもの
PAIRS_BODY = [(t, g) for t in ("--text", "--text-sub", "--text-muted", "--link") for g in ("--bg", "--surface", "--surface-2")] + [
    ("--mark-text", "--mark-bg"), ("--warn-text", "--warn-bg"), ("--error-text", "--error-bg"), ("--ok-text", "--ok-bg"),
    ("--on-accent", "--accent"), ("--on-accent", "--accent-hover"), ("--nav-text", "--nav-bg"), ("--nav-sub", "--nav-bg"),
    ("--link", "--band-rev"), ("--warn-text", "--band-main"), ("--error-text", "--surface"), ("--ok-text", "--surface"),
    ("--warn-text", "--surface")]
PAIRS_LARGE = [("--on-accent", "--warn-text"), ("--on-accent", "--ok-text"), ("--text-sub", "--mark-bg")]


def _check_pairs(names):
    bad = []
    for (t, g), need in [(p, 4.5) for p in PAIRS_BODY] + [(p, 3.0) for p in PAIRS_LARGE]:
        r = ratio(names[t], names[g])
        if r < need:
            bad.append((t, g, round(r, 2)))
    return bad


def test_t491_contrast_light():
    names = _defined_names()
    assert _check_pairs(names) == []
    for k in ("--bg", "--surface"):
        assert names[k].lower() != "#000000"
    assert names["--text"].lower() != "#ffffff"


# ── 492（§7-6）説明の表示の HTML に色を書かない ─────────────────────────────────────────
def test_t492_explanation_html_has_no_colors():
    fx = json.load(open(os.path.join(ROOT, "tests", "fixtures", "explanation_v0_4.json"), encoding="utf-8"))
    names = {"viewer": "アオイ", "target": "ハル"}
    htmls = [X.render(c["card"], "viewer", names) for c in fx["cases"]]
    forms = json.load(open(os.path.join(ROOT, "tests", "fixtures", "explanation_saved_forms.json"), encoding="utf-8"))
    htmls += [X.incoming_html(h, names) for f in forms["forms"].values() for h in f.values()]
    for h in [x for x in htmls if x]:
        assert "style=" not in h and not COLOR.search(h)                              # 色はクラスに当てる CSS だけ
