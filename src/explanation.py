"""接続の説明 — 段階1（固定のデータ・LLM なし）の契約と描き方（指示書68 §1・生成仕様 expl-v0.4）。

- validate(card): カードの JSON がスキーマ（schemas/explanation_card_v0.4.schema.json）に通るか
- check(card): 機械の確かめ（仕様 §6・§12-4 のうち段階1で通すもの）。落ちた部分を除いたカードと、落ちた理由を返す
  引用の実在・句の長さ・ラベル・R1／R2 の述語・① の根拠の印と閉じた語彙・禁止の語・呼び方・補充しない
- render_symbolic(card): 確かめを通った部分だけを HTML にする。呼び方は記号のまま（⟦V⟧＝見る人・⟦T⟧＝相手）
- personalize(html, reader, names): 読む人に合わせて記号を置き換える
  reader="viewer"（画面1・2）: 見る人＝「あなた」、相手＝「〇〇さん」
  reader="recipient"（画面3）: 一人称・二人称を使わない。両方ともユーザー名
  画面3 は、画面2 で保存した記号のままの HTML を、置き換えるだけで描く（再生成しない）

数値（g・gate・スコア）・生テキスト・根拠・evidence_span は扱わない。画面に例文を直接書かない（文はカードの JSON から）。
"""
import hashlib
import html
import json
import os
import re
import unicodedata

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schemas",
                           "explanation_card_v0.4.schema.json")
SCHEMA_VERSION = "expl-v0.4"
RELATION_RULE_VERSION = "rel-v0.3"
FORBIDDEN_VERSION = "forbid-v0.4"
QUOTE_MAX = 40

V, T = "⟦V⟧", "⟦T⟧"                      # 記号のままの HTML の中の呼び方（見る人・相手）

# 関係のラベル（仕様 §3-1・rel-v0.3）: (関係の名前, 裏付けの事実)
RELATIONS = {
    "R1": ("足りない役割を補える", "その役割を担ってきた（経験）"),
    "R2": ("足りない役割を補える", "その立場で関われると書いている（申告）"),
    "R3": ("必要な資源を提供できる", "その資源を持っている"),
    "R4": ("同じ関心でつながれる（補完ではない）", "関心が同じ"),
}
# R1・R2 の述語（仕様 §12-4 の 10）
R1_PRED = re.compile(r"てきた")
R2_PRED = re.compile(r"できる|関われる|手伝える")
# 禁止の語（仕様 §6-6。版ごとに管理する）
FORBIDDEN_WORDS = ("相性", "おすすめ", "オススメ", "間違いなく", "最適", "今すぐ", "してください", "しましょう", "満たせる")
CONTACT = re.compile(r"https?://|www\.|[\w.+-]+@[\w-]+\.[\w.-]+|\d{2,4}-\d{2,4}-\d{3,4}")
# ① の閉じた語彙（仕様 §4-3・§12-4 の 11）: どの文も「〜可能性があります。」で終わる
MEANING_END = ("になれる可能性があります。", "に関われる可能性があります。")
NOT_COVERED_TEXT = "欠かせない要素（{keys}）に当たる文は、{who}の公開の文の中には見つかりません"
UNKNOWN_TEXT = "「{quote}」に当たる経験は、{who}の公開の文の中には見つかりません"
FOOTER_TEXT = "AI による説明です。根拠は二人の公開の文です。機械の確かめは正しさを保証しません。"
DELETED_TEXT = "本文は削除されました"
SUPPLEMENT_BUTTON = '<button type="button" class="ex-supp-btn">補足する ›</button>'
HIDDEN_STATUSES = ("pending",)


# ── 契約 ─────────────────────────────────────────────────────────────────────────
def schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def validate(card):
    """スキーマに通るか。戻り値: (通ったか, 理由の一覧)。"""
    from jsonschema import Draft202012Validator
    errs = sorted(Draft202012Validator(schema()).iter_errors(card), key=lambda e: list(e.path))
    return (not errs), [f"{'/'.join(str(p) for p in e.path) or '(全体)'}: {e.message}" for e in errs]


def output_digest(card):
    """output_digest を除いたカードを正準化した JSON（キーの順・区切りを固定・NFC）の sha256。"""
    body = {k: v for k, v in card.items() if k != "output_digest"}
    s = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(unicodedata.normalize("NFC", s).encode("utf-8")).hexdigest()


# ── 機械の確かめ（仕様 §6・§12-4）────────────────────────────────────────────────────
def _side_parts(ref):
    side, kind, rest = ref.split(".", 2)
    return side, kind, rest


def _purpose_key(purpose_ref):
    return purpose_ref.split(".", 2)[2]                 # "viewer.purpose.p1" → "p1"


def _belongs(need_ref, purpose_ref):
    """必要の参照が、その目的のものか（補充しない。仕様 §12-4 の 14）。need の参照は <側>.need.<目的>.<要素>。"""
    side, kind, rest = _side_parts(need_ref)
    return kind == "need" and side == purpose_ref.split(".")[0] and rest.split(".")[0] == _purpose_key(purpose_ref)


def _forbidden(text):
    return [w for w in FORBIDDEN_WORDS if w in text] + (["連絡先・URL"] if CONTACT.search(text) else [])


def _check_meaning(meaning, kept, purpose):
    """① の確かめ（仕様 §6 の 4・5・6・7・8・§12-4 の 11）。戻り値: 理由の一覧（空なら通る）。"""
    why = []
    text, cites = meaning["text"], meaning["cites"]
    if not cites or any(c not in kept for c in cites):
        why.append("① の根拠の印が、確かめ済みの組を指していない")
    if "あなた" in text:
        why.append("① に「あなた」がある（呼び方は {N}・{O} だけ）")
    if "{O}" not in text:
        why.append("① に {O} が無い")
    bad = _forbidden(text)
    if bad:
        why.append(f"① に禁止の語がある（{'・'.join(bad)}）")
    sentences = [s for s in re.split(r"(?<=。)", text) if s.strip()]
    if not sentences or any(not s.endswith(MEANING_END) for s in sentences):
        why.append("① の文の終わりが閉じた語彙に無い")
    quotes = "".join(e["need_quote"] + e["offer_quote"] for i, e in enumerate(purpose["elements"]) if i in cites)
    digits = set(re.findall(r"[0-9０-９]+", text.replace("{N}", "").replace("{O}", "")))
    if any(d not in quotes and d not in purpose["title"] for d in digits):
        why.append("① に、組の句・見出しに無い数値がある")
    return why


def check(card, names=()):
    """確かめに落ちた部分を除いたカード（コピー）と、落ちた理由の一覧を返す。

    - 組（要素の対）: 引用の実在・句の長さ・ラベル・R1／R2 の述語・向き・その目的のものか（補充しない）・連絡先
    - ①: 根拠の印・閉じた語彙・禁止の語・呼び方・名前を含まない。外れたら、その目的の説明（①と組）を出さない
    - 補完の目的で R4 だけの組なら、その目的を explained にしない（出さない）
    - not_covered の目的に ① があれば、① を出さない
    """
    src = {c["ref"]: c["text"] for c in card.get("citations", [])}
    problems = []
    out = json.loads(json.dumps(card))
    for key, n_side, o_side in (("purposes", "viewer", "target"), ("reverse_purposes", "target", "viewer")):
        kept_purposes = []
        for p in out[key]:
            tag = f"{key}:{p['ref']}"
            if not p["ref"].startswith(f"{n_side}.purpose."):
                problems.append(f"{tag}: 向きが違う目的")
                continue
            kept, elements = {}, []
            for i, e in enumerate(p["elements"]):
                why = []
                if not _belongs(e["need_ref"], p["ref"]):
                    why.append("ほかの目的の要素（補充しない）")
                o_side_ref, o_kind, _ = _side_parts(e["offer_ref"])
                if o_side_ref != o_side or o_kind not in ("offer", "state", "interest"):
                    why.append("相手の文の参照が違う")
                for q, ref in ((e["need_quote"], e["need_ref"]), (e["offer_quote"], e["offer_ref"])):
                    if len(q) > QUOTE_MAX:
                        why.append(f"句が {QUOTE_MAX} 字を超える")
                    if ref not in src or q not in src[ref]:
                        why.append("引用が原文に無い")
                    if CONTACT.search(q):
                        why.append("句に連絡先・URL がある")
                if e["relation"] not in RELATIONS:
                    why.append("ラベルが R1〜R4 に無い")
                elif e["relation"] == "R1" and not R1_PRED.search(e["offer_quote"]):
                    why.append("R1 なのに経験の表現が無い")
                elif e["relation"] == "R2" and not R2_PRED.search(e["offer_quote"]):
                    why.append("R2 なのに申告の表現が無い")
                if why:
                    problems.append(f"{tag}: 組 {i} を出さない（{'・'.join(dict.fromkeys(why))}）")
                    continue
                kept[i] = len(elements)
                elements.append(e)
            unknown = [u for u in p["unknown"] if _belongs(u["need_ref"], p["ref"]) and len(u["need_quote"]) <= QUOTE_MAX
                       and u["need_ref"] in src and u["need_quote"] in src[u["need_ref"]]]
            if len(unknown) != len(p["unknown"]):
                problems.append(f"{tag}: 分からないことの一部を出さない（引用・目的・句の長さ）")
            missing = [m for m in p["missing_must"] if _belongs(m, p["ref"])]
            if len(missing) != len(p["missing_must"]):
                problems.append(f"{tag}: 欠けている要素の一部を出さない（ほかの目的の要素）")
            if p["status"] == "explained":
                why = []
                if p["kind"] == "補完" and elements and all(e["relation"] == "R4" for e in elements):
                    why.append("補完の目的で R4 だけ")
                if not elements:
                    why.append("確かめを通った組が無い")
                if p["meaning"] is None:
                    why.append("① が無い")
                else:
                    why += _check_meaning(p["meaning"], kept, p)
                    why += [f"① に名前がある（{n}）" for n in names if n and n in p["meaning"]["text"]]
                if why:
                    problems.append(f"{tag}: この目的の説明を出さない（{'・'.join(why)}）")
                    continue
                p["meaning"] = {"text": p["meaning"]["text"], "cites": [kept[c] for c in p["meaning"]["cites"]]}
            elif p["meaning"] is not None:
                problems.append(f"{tag}: {p['status']} の目的の ① を出さない")
                p["meaning"] = None
            p["elements"], p["unknown"], p["missing_must"] = elements, unknown, missing
            kept_purposes.append(p)
        out[key] = kept_purposes
    return out, problems


def showable(card):
    """表示するか（仕様 §4-1・§12-2）: pending は出さない。どちらの向きにも explained が無ければ出さない。deleted は印だけ出す。"""
    if card["status"] in HIDDEN_STATUSES:
        return False
    if card["status"] == "deleted":
        return True
    return any(p["status"] == "explained" for p in card["purposes"] + card["reverse_purposes"])


# ── 描き方 ─────────────────────────────────────────────────────────────────────────
def _e(s):
    return html.escape(str(s), quote=True)


def _sym(text, n, o):
    """生成した文の記号 {N}・{O} を、向きに合わせて ⟦V⟧・⟦T⟧ にする（エスケープの後）。"""
    return _e(text).replace("{N}", n).replace("{O}", o)


def _quote(q, ref, src):
    """引用の句と、原文を 1 回の操作で開く印（仕様 §7-1「根拠の印から、原文をいつでも開ける」）。"""
    return (f'<span class="ex-q">「{_e(q)}」</span><details class="ex-src"><summary>原文</summary>'
            f'<span class="ex-src-text">{_e(src.get(ref, ""))}</span></details>')


def _elem_key(ref):
    return ref.rsplit(".", 1)[-1]


_REF_KIND = {"need": "必要としている相手", "offer": "力になれること", "state": "現状", "interest": "関心", "purpose": "目的"}


def _ref_label(ref):
    """参照の名前を、読める形にする（例: target.offer.2 → ⟦T⟧の力になれること（2））。"""
    side, kind, rest = _side_parts(ref)
    return f"{V if side == 'viewer' else T}の{_REF_KIND.get(kind, kind)}（{_e(rest.split('.')[-1])}）"


def _purpose_html(p, n, o, src, reverse, has_purposes=True):
    if p["status"] == "pending":
        return ""
    has_must = any(e["must"] for e in p["elements"]) or bool(p["missing_must"])
    if not p["elements"] and not has_must:
        return ""                                       # 欠かせない要素が無く、組も無い目的は出さない（仕様 §4-2）
    if reverse and not has_purposes:                    # 相手が v4（目的が無い）: 求めている相手の文を見出しにする（仕様 §4-6）
        head = f'<h4 class="ex-ph">■ {n}が求めている相手『{_e(p["title"])}』について</h4>'
    else:
        head = f'<h4 class="ex-ph">■ {n}の目的{p["number"]}「{_e(p["title"])}」について</h4>'
    body = []
    if p["status"] == "not_covered" or (p["status"] != "explained" and p["missing_must"]):
        keys = "・".join(_elem_key(m) for m in p["missing_must"]) or "—"
        body.append(f'<p class="ex-nc">{NOT_COVERED_TEXT.format(keys=keys, who=o)}</p>')
    if p["status"] == "explained":
        marks = f'<sup class="ex-cite">{",".join(str(c + 1) for c in p["meaning"]["cites"])}</sup>'
        body.append(f'<p class="ex-meaning">{_sym(p["meaning"]["text"], n, o)}{marks}</p>')
        rows = []
        for i, e in sorted(enumerate(p["elements"]), key=lambda x: (not x[1]["must"], x[0])):   # 欠かせない要素を先に
            name, basis = RELATIONS[e["relation"]]
            kind = "欠かせない要素" if e["must"] else "あれば嬉しい要素"
            rows.append(
                f'<li class="ex-el" data-rel="{e["relation"]}"><div class="ex-k">{"●" if e["must"] else "○"} '
                f'{kind}（{_e(_elem_key(e["need_ref"]))}）<sup class="ex-cite">{i + 1}</sup></div>'
                f'<div class="ex-pair">{n}：{_quote(e["need_quote"], e["need_ref"], src)}</div>'
                f'<div class="ex-pair">{o}：{_quote(e["offer_quote"], e["offer_ref"], src)}</div>'
                f'<div class="ex-rel">［{_e(name)}］← {_e(basis)}</div></li>')
        body.append('<div class="ex-sub">── その根拠として ──</div><ul class="ex-els">' + "".join(rows) + "</ul>")
    if p["unknown"]:
        items = "".join(f'<li>{UNKNOWN_TEXT.format(quote=_e(u["need_quote"]), who=o)}</li>' for u in p["unknown"])
        body.append(f'<div class="ex-sub">── 文からは分からないこと ──</div><ul class="ex-unk">{items}</ul>')
    if not body:
        return ""
    cls = "ex-purpose ex-rev" if reverse else "ex-purpose"
    return f'<div class="{cls}" data-ref="{_e(p["ref"])}" data-status="{_e(p["status"])}">{head}{"".join(body)}</div>'


def render_symbolic(card, names=()):
    """確かめを通った部分だけの HTML（呼び方は記号 ⟦V⟧・⟦T⟧ のまま）。表示しないときは None。

    画面2 で保存するのはこの HTML（を見る人に合わせて置き換えたもの）。画面3 は、この HTML を置き換えるだけで描く。"""
    checked, _ = check(card, names=names)
    if not showable(checked):
        return None
    if checked["status"] == "deleted":
        return f'<section class="ex-card" data-status="deleted"><p class="ex-deleted">{DELETED_TEXT}</p></section>'
    src = {c["ref"]: c["text"] for c in checked["citations"]}
    parts = []
    if checked["status"] == "stale" and checked.get("stale"):
        changed = "・".join(_ref_label(r) for r in checked["stale"]["changed_refs"]) or "—"
        parts.append(f'<p class="ex-stale">承認時の説明（{_e(checked["stale"]["approved_at"])}）。'
                     f'現在のプロフィールと違う欄: {changed}</p>')
    # 目的はプロフィールの順で固定（成否で並べ替えない。仕様 §4-2）
    for p in sorted(checked["purposes"], key=lambda x: x["order"]):
        parts.append(_purpose_html(p, V, T, src, reverse=False))
    rev = [h for h in (_purpose_html(p, T, V, src, reverse=True, has_purposes=checked["target_has_purposes"])
                       for p in sorted(checked["reverse_purposes"], key=lambda x: x["order"])) if h]
    if rev:
        head = f"{T}から見た{V}" if checked["target_has_purposes"] else f"{T}が求めている相手"
        parts.append(f'<div class="ex-sub ex-revh">── {head} ──</div>' + "".join(rev))
    for s in checked.get("supplements") or []:
        who = V if s["by"] == "viewer" else T
        parts.append(f'<p class="ex-supp">{who}の補足（{_e(s["at"])}）：「{_e(s["text"])}」</p>')
    parts.append(f'<p class="ex-foot">{FOOTER_TEXT}</p>' + SUPPLEMENT_BUTTON)
    return (f'<section class="ex-card" data-schema="{SCHEMA_VERSION}" data-status="{_e(checked["status"])}">'
            + "".join(x for x in parts if x) + "</section>")


def personalize(symbolic_html, reader, names):
    """記号のままの HTML を、読む人に合わせて置き換える（これだけ。再生成しない）。

    reader="viewer": 見る人＝「あなた」、相手＝「〇〇さん」（画面1・2）
    reader="recipient": 両方ともユーザー名（画面3。一人称・二人称を使わない）
    names = {"viewer": 見る人の表示名, "target": 相手の表示名}"""
    if symbolic_html is None:
        return None
    t = f"{_e(names['target'])} さん"
    v = "あなた" if reader == "viewer" else f"{_e(names['viewer'])} さん"
    if reader not in ("viewer", "recipient"):
        raise ValueError("reader は viewer か recipient")
    return symbolic_html.replace(V, v).replace(T, t)


def render(card, reader, names):
    """確かめ → 記号のままの HTML → 置き換え。"""
    return personalize(render_symbolic(card, names=tuple(names.values())), reader, names)


# ── 向きのレコード（指示書68 §1-2: 説明は向きごとに 1 つ。画面で組み立てる）──────────────────────────
# 向き＝誰の目的 × 誰の文。記号は N（必要を出した側）・O（文を出した側）。カードの viewer／target には、
# 組み立てるときに置き換える。A の目的 × B の文 の向きは、A の画面の本体と、B の画面の「A さんから見た B さん」の両方に使う。
DIRECTION_FIELDS = ("purposes", "citations", "as_of", "generation")


def _swap_ref(ref, mapping):
    side, rest = ref.split(".", 1)
    return f"{mapping[side]}.{rest}"


def _map_purposes(purposes, mapping):
    out = []
    for p in purposes:
        q = json.loads(json.dumps(p))
        q["ref"] = _swap_ref(q["ref"], mapping)
        for e in q["elements"]:
            e["need_ref"], e["offer_ref"] = _swap_ref(e["need_ref"], mapping), _swap_ref(e["offer_ref"], mapping)
        for u in q["unknown"]:
            u["need_ref"] = _swap_ref(u["need_ref"], mapping)
        q["missing_must"] = [_swap_ref(m, mapping) for m in q["missing_must"]]
        out.append(q)
    return out


def split_card(card):
    """カード（見る人 × 相手）を、向きのレコード 2 つ（見る人の目的 × 相手の文・相手の目的 × 見る人の文）に分ける。
    引用は、必要・目的の欄は N の側の向きに、文の欄（offer・state・interest）は O の側の向きに入れる。"""
    fwd = {"purposes": _map_purposes(card["purposes"], {"viewer": "N", "target": "O"}), "citations": [],
           "as_of": {"need": card["as_of"]["viewer"], "offer": card["as_of"]["target"]}, "generation": card["generation"]}
    rev = {"purposes": _map_purposes(card["reverse_purposes"], {"target": "N", "viewer": "O"}), "citations": [],
           "as_of": {"need": card["as_of"]["target"], "offer": card["as_of"]["viewer"]}, "generation": card["generation"]}
    for c in card["citations"]:
        side, kind, _ = _side_parts(c["ref"])
        need_side = kind in ("need", "purpose")
        to_fwd = (side == "viewer") == need_side
        d, m = (fwd, {"viewer": "N", "target": "O"}) if to_fwd else (rev, {"target": "N", "viewer": "O"})
        d["citations"].append({"ref": _swap_ref(c["ref"], m), "text": c["text"]})
    return fwd, rev


def assemble(fwd, rev, target_has_purposes=True):
    """向きのレコード 2 つ（fwd＝見る人の目的 × 相手の文・rev＝相手の目的 × 見る人の文。無ければ None）から、
    見る人 × 相手のカードを組み立てる。output_digest を付けて返す。どちらも無ければ None。"""
    if not fwd and not rev:
        return None
    fm, rm = {"N": "viewer", "O": "target"}, {"N": "target", "O": "viewer"}
    cites, seen = [], set()
    for d, m in ((fwd, fm), (rev, rm)):
        for c in (d or {}).get("citations") or []:
            ref = _swap_ref(c["ref"], m)
            if ref not in seen:
                seen.add(ref)
                cites.append({"ref": ref, "text": c["text"]})
    base = fwd or rev
    as_of = {"viewer": (fwd or {}).get("as_of", {}).get("need") or (rev or {}).get("as_of", {}).get("offer") or {},
             "target": (fwd or {}).get("as_of", {}).get("offer") or (rev or {}).get("as_of", {}).get("need") or {}}
    purposes = _map_purposes((fwd or {}).get("purposes") or [], fm)
    reverse = _map_purposes((rev or {}).get("purposes") or [], rm)
    status = "explained" if any(p["status"] == "explained" for p in purposes + reverse) else "not_covered"
    card = {"schema_version": SCHEMA_VERSION, "relation_rule_version": RELATION_RULE_VERSION, "status": status,
            "show_reason": None, "as_of": as_of, "generation": base["generation"],
            "target_has_purposes": bool(target_has_purposes), "purposes": purposes, "reverse_purposes": reverse,
            "citations": cites}
    card["output_digest"] = output_digest(card)
    return card


def short_form(card, names=()):
    """一覧の短い形（指示書68 §2-2）: 説明のある目的の見出しと ① の 1 文（記号のまま・HTML エスケープ済み）。
    見る人の目的を先に、無ければ逆の向き。確かめを通った部分だけ。出すものが無ければ None。"""
    checked, _ = check(card, names=names)
    if not showable(checked) or checked["status"] == "deleted":
        return None
    def items_of(purposes, n, o, reverse):
        out = []
        for p in sorted(purposes, key=lambda x: x["order"]):
            if p["status"] != "explained":
                continue
            first = next((x for x in re.split(r"(?<=。)", p["meaning"]["text"]) if x.strip()), "")
            if reverse and not checked["target_has_purposes"]:
                head = f"{n}が求めている相手『{_e(p['title'])}』"
            else:
                head = f"{n}の目的{p['number']}「{_e(p['title'])}」"
            out.append({"head": head, "meaning": _sym(first, n, o), "reverse": reverse})
        return out
    # 見る人の目的の説明を先に。無ければ、相手の目的 × 見る人の文（逆の向き）の説明を出す
    return items_of(checked["purposes"], V, T, False) or items_of(checked["reverse_purposes"], T, V, True) or None


def supplements_html(supplements):
    """承認のときの補足（本人の言葉）を、記号のままの HTML にする（⟦V⟧＝申請した人・⟦T⟧＝承認した人）。"""
    out = []
    for s in supplements:
        who = V if s["by"] == "viewer" else T
        out.append(f'<p class="ex-supp">{who}の補足（{_e(s["at"])}）：「{_e(s["text"])}」</p>')
    return "".join(out)
