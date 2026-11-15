"""指示書75 — 色の名前の一本化（順1）とダークモード（順2）。テスト 489〜。

489（§7-1）テンプレートと、HTML を出すコードに、色の直書きが残っていない（_theme.html の色の定義を除く）
490（§7-2）色の名前がそろっている（参照している名前はすべて定義がある。全ページが _theme.html を読み込む）
491（§7-3）文字と地の組のコントラスト比が §3 の線を満たす（明るい表示）
492（§7-6）説明の表示の HTML には色を書かない（クラスだけ。保存済みの見本と、新しく描いたもの）
493（§7-2）色の名前のすべてに、明るい色と暗い色がある（暗い色の定義は _theme.html の 1 か所だけ）
494（§7-3）文字と地の組のコントラスト比が §3 の線を満たす（暗い表示）。地は真っ黒にしない・文字は真っ白にしない
495（§7-4）ナビにボタンがあり（コミュニティの右・SVG の月と太陽）、aria-label が状態で切り替わる。狭い幅は ☰ の中の 1 行
496（§7-5）保存した選択があれば、それを使う。無ければ、端末の設定に合わせる。ページを描く前（<head> の先頭）に決める
497（§7-7）サーバーへの保存・クッキーが増えていない（選択はブラウザの中の localStorage だけ）
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


def _theme_css():
    s = open(THEME, encoding="utf-8").read()
    return re.sub(r"/\*.*?\*/", "", re.sub(r"\{#.*?#\}", "", s, flags=re.S), flags=re.S)


BLOCK = {"light": r":root\s*\{([^{}]*)\}", "dark": r":root\[data-theme=\"dark\"\]\s*\{([^{}]*)\}"}


def _defined_names(theme="light"):
    """_theme.html の、明るい表示（:root）か暗い表示（:root[data-theme="dark"]）の色の名前と値。"""
    blocks = re.findall(BLOCK[theme], _theme_css())
    assert len(blocks) == 1, (theme, len(blocks))
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", blocks[0]))


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


# ── 493（§7-2）すべての名前に、明るい色と暗い色がある ─────────────────────────────────────
def test_t493_every_name_has_light_and_dark():
    light, dark = _defined_names("light"), _defined_names("dark")
    assert set(light) == set(dark), (set(light) ^ set(dark))                       # 同じ名前の組
    assert all(COLOR.search(v) for v in dark.values()), dark
    assert any(light[k].lower() != dark[k].lower() for k in light)
    css = _theme_css()
    assert re.search(r":root\s*\{[^{}]*color-scheme:\s*light", css)                 # 入力欄・スクロールバーの既定の色
    assert re.search(r":root\[data-theme=\"dark\"\]\s*\{[^{}]*color-scheme:\s*dark", css)
    # 暗い色の定義はこの 1 か所だけ（ほかのテンプレートは暗い表示のときに色の名前を定義し直さない・端末の設定を読まない）
    for path in glob.glob(os.path.join(TPL, "*.html")):
        if path == THEME:
            continue
        t = open(path, encoding="utf-8").read()
        assert "prefers-color-scheme" not in t, os.path.basename(path)
        for m in re.finditer(r"data-theme=\\?\"dark\\?\"\]([^{]*)\{([^}]*)\}", t):
            assert not re.search(r"--[a-z0-9-]+\s*:", m.group(2)) and not COLOR.search(m.group(2)), (path, m.group(0))


# ── 494（§7-3）コントラスト比（暗い表示）───────────────────────────────────────────────
def test_t494_contrast_dark():
    names = _defined_names("dark")
    assert _check_pairs(names) == []
    for k in ("--bg", "--surface", "--surface-2"):
        assert names[k].lower() not in ("#000", "#000000")                              # 地は真っ黒にしない
    for k in ("--text", "--text-sub", "--nav-text"):
        assert names[k].lower() not in ("#fff", "#ffffff")                              # 文字は真っ白にしない
    # ナビの紺を基調にする（暗い地も青みのある色。r < b）
    for k in ("--bg", "--surface", "--nav-bg"):
        h = names[k].lstrip("#")
        assert int(h[0:2], 16) < int(h[4:6], 16), k
    # 影は暗い地では薄くする（明るい表示より薄い）。影を使う部品（☰ のメニュー）は縁の線で見分ける
    alpha = lambda v: float(v.replace(" ", "").split(",")[3].rstrip(")"))            # noqa: E731
    assert alpha(names["--shadow"]) < alpha(_defined_names("light")["--shadow"])
    nav = open(os.path.join(TPL, "_nav.html"), encoding="utf-8").read()
    for path in glob.glob(os.path.join(TPL, "*.html")):
        t = open(path, encoding="utf-8").read()
        for m in re.finditer(r'style="[^"]*var\(--shadow\)[^"]*"', t):
            assert "border:1px solid var(--line" in m.group(0), (path, m.group(0)[:80])
    assert "var(--shadow)" in nav


# ── 495（§7-4）ナビのボタン ───────────────────────────────────────────────────────────
def test_t495_nav_toggle_button():
    nav = open(os.path.join(TPL, "_nav.html"), encoding="utf-8").read()
    i_comm, i_btn, i_login = nav.index('id="navCommunities"'), nav.index('id="navTheme"'), nav.index('id="navLogin"')
    assert i_comm < i_btn < i_login                                                    # コミュニティの右
    btn = nav[nav.rindex("<button", 0, i_btn):nav.index("</button>", i_btn)]
    assert 'aria-label="表示を暗くする"' in btn and 'type="button"' in btn
    assert btn.count("<svg") == 2 and 'class="pox-moon"' in btn and 'class="pox-sun"' in btn   # 埋め込みの SVG（月と太陽）
    assert not re.search("[\U0001F300-\U0001FAFF\u2600-\u27BF]", btn)                  # 絵文字ではない
    # 明るい表示では月・暗い表示では太陽を見せる
    assert re.search(r":root\[data-theme=\"dark\"\] \.pox-theme-btn \.pox-moon\{ ?display:none", nav)
    assert re.search(r":root:not\(\[data-theme=\"dark\"\]\) \.pox-theme-btn \.pox-sun\{ ?display:none", nav)
    # aria-label を状態で切り替える
    js = nav[nav.index("function syncThemeBtn"):nav.index("syncThemeBtn();", nav.index("function toggleTheme"))]
    assert '"表示を明るくする"' in js and '"表示を暗くする"' in js and 'setAttribute("aria-label", label)' in js
    # 狭い幅（iPhone の幅・430 以下）では、ナビのボタンを隠して ☰ の中の 1 行で切り替える（報告75 の案 B）
    menu = nav[nav.index('id="navMenu"'):nav.index("</div>\n  </div>", nav.index('id="navMenu"'))]
    assert 'id="menuTheme"' in menu and menu.count("<svg") == 2 and "表示を暗くする" in menu
    assert re.search(r"@media \(max-width:\s*430px\)\s*\{[^}]*\.pox-theme-btn\{ ?display:none;?\s*\}[^}]*"
                     r"\.pox-theme-menu\{ ?display:block;?\s*\}", nav)
    assert 'themeBtn.addEventListener("click", toggleTheme)' in nav and 'themeMenu.addEventListener("click", toggleTheme)' in nav
    assert nav.count('id="navTheme"') == 1 and nav.count('id="menuTheme"') == 1        # 全ページ共通の部品に 1 つ
    for path in glob.glob(os.path.join(TPL, "*.html")):
        if not path.endswith("_nav.html"):
            assert "navTheme" not in open(path, encoding="utf-8").read(), path


# ── 496（§7-5）最初の見え方・ちらつき ──────────────────────────────────────────────────
def test_t496_initial_theme_from_saved_or_device():
    s = open(THEME, encoding="utf-8").read()
    head = s[:s.index("<style>")]                                                        # 色より前のスクリプト
    js = head[head.index("<script>"):head.index("</script>")]
    assert 'localStorage.getItem("pox_theme")' in js                                    # 保存した選択があればそれ
    assert 't !== "dark" && t !== "light"' in js                                        # 無い・変な値なら
    assert 'matchMedia("(prefers-color-scheme: dark)")' in js                           # 端末の設定
    assert 'document.documentElement.setAttribute("data-theme", t)' in js
    assert "try {" in js                                                                 # localStorage が使えなくても描ける
    assert "async" not in head and "defer" not in head and " src=" not in head          # 描く前にその場で走る
    # <head> の先頭（ほかの style・script・link より前）で読み込む
    for name in ("_base.html", "login.html", "index.html"):
        t = open(os.path.join(TPL, name), encoding="utf-8").read()
        h = t[t.index("<head>"):t.index("</head>")]
        i = h.index('{% include "_theme.html" %}')
        assert all(h.find(tag) == -1 or h.find(tag) > i for tag in ("<style", "<script", "<link")), name
    # ナビの切り替えは同じ鍵に、明るい／暗いの 2 つだけを書く（3 つ目の「端末に合わせる」は作らない）
    nav = open(os.path.join(TPL, "_nav.html"), encoding="utf-8").read()
    assert 'localStorage.setItem("pox_theme", next)' in nav
    assert 'getAttribute("data-theme") === "dark" ? "light" : "dark"' in nav
    assert "removeItem(\"pox_theme\")" not in nav


# ── 497（§7-7）サーバーへの保存・クッキーが増えていない ─────────────────────────────────────
def test_t497_no_server_storage_or_cookie():
    for path in glob.glob(os.path.join(TPL, "*.html")):
        t = open(path, encoding="utf-8").read()
        assert "document.cookie" not in t, os.path.basename(path)
    theme_js = open(THEME, encoding="utf-8").read() + open(os.path.join(TPL, "_nav.html"), encoding="utf-8").read()
    js = theme_js[theme_js.index("function syncThemeBtn"):theme_js.index("syncThemeBtn();", theme_js.index("function toggleTheme"))]
    assert "fetch(" not in js and "XMLHttpRequest" not in js and "sendBeacon" not in js
    for path in [os.path.join(ROOT, "app.py")] + sorted(glob.glob(os.path.join(ROOT, "src", "*.py"))):
        t = open(path, encoding="utf-8").read()
        assert "pox_theme" not in t and "data-theme" not in t and not re.search(r"\btheme\b", t), path
    # Flask の応答は、表示の選択でクッキーを付けない（ログイン前のページを開いても Set-Cookie に theme が無い）
    import importlib
    sys.modules.pop("app", None)
    app = importlib.import_module("app").app
    c = app.test_client()
    for url in ("/", "/login", "/privacy", "/communities"):
        r = c.get(url)
        assert "theme" not in " ".join(r.headers.getlist("Set-Cookie")).lower(), url
