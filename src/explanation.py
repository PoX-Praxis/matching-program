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
                           "explanation_card_v0.7.schema.json")
SCHEMA_VERSION = "expl-v0.7"                          # 指示書73 順3: 目的の status に open を足した（v0.4 のファイルも残す）
SHOWN_STATUSES = ("explained", "open")                # ① と組を出す目的の状態（指示書73 §1）
RELATION_RULE_VERSION = "rel-v0.4"                    # 指示書72: R1・R2 の述語を広げた（仕様 §14 v0.6）
FORBIDDEN_VERSION = "forbid-v0.5"                      # 指示書81 追補1: 「同種」とラベルの英語の名前を足した
QUOTE_MAX = 40
# 説明の生成の流れ（指示書81。ラベル v0.3 → 選ぶ・まとめる v0.1）で作ったカードの印: generation.prompt1_sha がこの値。
# このカードには、仕様 v0.4 の形の決まり（句の長さ・R1／R2 の述語・① の閉じた語彙・{O} が必ずある）を当てない。
# 安全の確かめ（引用の実在・目的への所属・向き・連絡先・禁止の語・名前・「あなた」・組に無い数値・根拠の印）は当てる
PIPELINE_PROMPT1_SHA = "02ec1ca8362431e8caafd0dd59c62e461e532e53ec5632f4d16beb268c7e9f6e"

V, T = "⟦V⟧", "⟦T⟧"                      # 記号のままの HTML の中の呼び方（見る人・相手）

# 関係のラベル（仕様 §3-1・rel-v0.3）: (関係の名前, 裏付けの事実)
RELATIONS = {
    "R1": ("足りない役割を補える", "その役割を担ってきた（経験）"),
    "R2": ("足りない役割を補える", "その立場で関われると書いている（申告）"),
    "R3": ("必要な資源を提供できる", "その資源を持っている"),
    "R4": ("同じ関心でつながれる（補完ではない）", "関心が同じ"),
}
# R1・R2 の述語（仕様 §12-4 の 10 を §14 v0.6・rel-v0.4 で改める。指示書72・報告72 の確認 2026-11-16）
# R1（経験）: 過去の経験を表す文末。願望（〜したい）・思い（〜と思いました）・変化（〜ようになりました）・
#   続いていた状態（〜ていた・〜ていました）・関係の無い過去（〜だった・〜でした・〜かった）は通さない
R1_PRED = re.compile(
    r"(?:て|んで|いで)き(?:た|ました)"                                                  # 〜てきた・〜んできた・〜てきました
    r"|(?<!思い)(?<!願い)(?<!考え)(?<!感じ)(?<!ようになり)(?<!てい)(?<!でい)ました"       # 〜しました（除く形は上のとおり）
    r"|(?<!だっ)(?<!かっ)(?<!思っ)(?<!願っ)(?<!でし)(?<!まし)(?<!てい)(?<!でい)(?<!ようになっ)"
    r"た(?=[。、]|$)")                                                                  # 〜した・〜作った（文末・読点の前）
# R2（申告）: できる・関われる・手伝える と、その丁寧の形。願望（〜できるように）・言い切らない形（〜かも）は通さない
R2_PRED = re.compile(r"(?:でき|関われ|手伝え)(?:る|ます)(?!ように|かも)")
# 禁止の語（仕様 §6-6。版ごとに管理する）
FORBIDDEN_WORDS = ("相性", "おすすめ", "オススメ", "間違いなく", "最適", "今すぐ", "してください", "しましょう", "満たせる")
# ラベルの語（指示書81 追補1 §3-2）。英語の名前は大文字・小文字を区別しない
LABEL_WORDS = ("同種", "same_kind", "relational", "partial", "same_interest", "topic_only", "irrelevant", "unclear",
               "resolves", "eases", "worsens", "constrains")
FORBIDDEN_WORDS = FORBIDDEN_WORDS + LABEL_WORDS
# 流れのカード（指示書81 追補1 §3-2）の ① の各文の終わり（言い方を根拠より強くしない）
PIPELINE_MEANING_END = "可能性があります。"
CONTACT = re.compile(r"https?://|www\.|[\w.+-]+@[\w-]+\.[\w.-]+|\d{2,4}-\d{2,4}-\d{3,4}")
# ① の閉じた語彙（仕様 §4-3・§12-4 の 11）: どの文も「〜可能性があります。」で終わる
MEANING_END = ("になれる可能性があります。", "に関われる可能性があります。")
# ── 表示の文（指示書71・仕様 v0.5、指示書73・仕様 v0.7 の追記）。すべて決まった型で、LLM は使わない ───────────
# v0.4 の「見つかりません」の文は v0.5 で、v0.5 の「つながった後に確かめられること」は v0.7 で使わなくなった。
# 保存済みの表示（画面2・承認時）は HTML の文字列のまま読むので、古い文の定数は要らない（描き直さない）。
RENDER_VERSION = "v0.8"
FOOTER_TEXT = ("AI による説明です。並び順は近さの順ではありません。説明は、選ばれた理由ではなく、"
               "二人の公開の文から読み取った、つながる意味です。機械の確かめは正しさを保証しません。")
SUMMARY_MEANING = "{list}に、つながる意味があります。"           # 要旨（v0.7 §15-2）。つながる意味がある目的だけ
STATE_TEXT = {                                                   # 目的ごとの状態の 1 行（v0.7 §15-1。数値・割合は出さない）
    "all": "この目的の要素のすべてに、つながる意味があります。",
    "part": "この目的の要素の一部に、つながる意味があります。",
    "unknown": "この目的については、公開の文からは分かりません。",
}
BASIS_SUMMARY = "根拠を見る ›"                                   # 根拠・分からないこと・問いを畳む（v0.5 §13-3）
FACT_HEAD = "文からは分からないこと"                             # 分からないこと（事実だけ。v0.7 §15-3）
FACT_TEXT = "「{quote}」について、{who}の経験は公開の文からは分かりません。"
MUST_PREFIX = "欠かせない要素："
ASK_HEAD = "つながった後に尋ねること"                            # 問い（事実と混ぜない。v0.7 §15-4）
ASK_TEXT = "「{quote}」について、経験はありますか。いまは、関われる状況ですか。"
BASIS_HEAD = "根拠"                                             # 根拠の節の見出し（v0.8 §16-3。v0.7 までは「その根拠として」）
# 画面3 の組み直し（v0.8 §16-4）の印と見出し。見出しは決まった型で、LLM は使わない
MAIN_OPEN, MAIN_CLOSE = "<!--ex:main-->", "<!--/ex:main-->"
REV_OPEN, REV_CLOSE = "<!--ex:rev:{kind}-->", "<!--/ex:rev-->"   # kind: p＝相手に目的がある／s＝求めている相手（v4）
SIDE_HEAD_REV = {"p": "{t}（受けた人）の目的から見た、{v}", "s": "{t}（受けた人）が求めている相手から見た、{v}"}
SIDE_HEAD_MAIN = "{v}（申請した人）の目的から見た、{t}"
DELETED_TEXT = "本文は削除されました"
SUPPLEMENT_BUTTON = '<button type="button" class="ex-supp-btn">補足する ›</button>'
HIDDEN_STATUSES = ("pending",)


# ── 契約 ─────────────────────────────────────────────────────────────────────────
SCHEMA_FILES = {"expl-v0.4": "explanation_card_v0.4.schema.json", "expl-v0.7": "explanation_card_v0.7.schema.json"}


def schema(version=None):
    """カードのスキーマ。版を渡せば、その版のファイル（前の版のカードは前の版のスキーマで確かめる）。無ければいまの版。"""
    name = SCHEMA_FILES.get(version or SCHEMA_VERSION, os.path.basename(SCHEMA_PATH))
    with open(os.path.join(os.path.dirname(SCHEMA_PATH), name), encoding="utf-8") as f:
        return json.load(f)


def validate(card):
    """スキーマに通るか（カードの schema_version の版のスキーマで）。戻り値: (通ったか, 理由の一覧)。"""
    from jsonschema import Draft202012Validator
    version = card.get("schema_version") if isinstance(card, dict) else None
    errs = sorted(Draft202012Validator(schema(version)).iter_errors(card), key=lambda e: list(e.path))
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
    low = text.lower()
    return [w for w in FORBIDDEN_WORDS if w in text or (w.isascii() and w in low)] + (["連絡先・URL"] if CONTACT.search(text) else [])


def is_pipeline(card):
    """説明の生成の流れ（指示書81）で作ったカードか。"""
    return ((card or {}).get("generation") or {}).get("prompt1_sha") == PIPELINE_PROMPT1_SHA


def _check_meaning(meaning, kept, purpose, pipeline=False):
    """① の確かめ（仕様 §6 の 4・5・6・7・8・§12-4 の 11）。戻り値: 理由の一覧（空なら通る）。
    pipeline=True（指示書81 のカード）: 形の決まり（{O} が必ずある・閉じた語彙）は当てない。安全の確かめは当てる。"""
    why = []
    text, cites = meaning["text"], meaning["cites"]
    if not cites or any(c not in kept for c in cites):
        why.append("① の根拠の印が、確かめ済みの組を指していない")
    if "あなた" in text:
        why.append("① に「あなた」がある（呼び方は {N}・{O} だけ）")
    if "{O}" not in text and not pipeline:
        why.append("① に {O} が無い")
    bad = _forbidden(text)
    if bad:
        why.append(f"① に禁止の語がある（{'・'.join(bad)}）")
    sentences = [s for s in re.split(r"(?<=。)", text) if s.strip()]
    if not sentences or (not pipeline and any(not s.endswith(MEANING_END) for s in sentences)):
        why.append("① の文の終わりが閉じた語彙に無い")
    elif pipeline and any(not s.strip().endswith(PIPELINE_MEANING_END) for s in sentences):
        why.append("① の文の終わりが「可能性があります。」でない")
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
    pipe = is_pipeline(card)                     # 指示書81 のカード: 形の決まりを当てない（安全の確かめは当てる）
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
                # 流れのカードは、目的の文そのもの・（目的の欄が無い人の）縛られているもの（指示書81 追補1 §3-3）も要素
                own_bound = pipe and e["need_ref"] == f"{p['ref'].split('.')[0]}.state.縛られているもの"
                if not (_belongs(e["need_ref"], p["ref"]) or (pipe and e["need_ref"] == p["ref"]) or own_bound):
                    why.append("ほかの目的の要素（補充しない）")
                o_side_ref, o_kind, _ = _side_parts(e["offer_ref"])
                if o_side_ref != o_side or o_kind not in ("offer", "state", "interest"):
                    why.append("相手の文の参照が違う")
                for q, ref in ((e["need_quote"], e["need_ref"]), (e["offer_quote"], e["offer_ref"])):
                    if len(q) > QUOTE_MAX and not pipe:
                        why.append(f"句が {QUOTE_MAX} 字を超える")
                    if ref not in src or q not in src[ref]:
                        why.append("引用が原文に無い")
                    if CONTACT.search(q):
                        why.append("句に連絡先・URL がある")
                if e["relation"] not in RELATIONS:
                    why.append("ラベルが R1〜R4 に無い")
                elif e["relation"] == "R1" and not pipe and not R1_PRED.search(e["offer_quote"]):
                    why.append("R1 なのに経験の表現が無い")
                elif e["relation"] == "R2" and not pipe and not R2_PRED.search(e["offer_quote"]):
                    why.append("R2 なのに申告の表現が無い")
                if why:
                    problems.append(f"{tag}: 組 {i} を出さない（{'・'.join(dict.fromkeys(why))}）")
                    continue
                kept[i] = len(elements)
                elements.append(e)
            unknown = [u for u in p["unknown"] if _belongs(u["need_ref"], p["ref"]) and (pipe or len(u["need_quote"]) <= QUOTE_MAX)
                       and u["need_ref"] in src and u["need_quote"] in src[u["need_ref"]]]
            if len(unknown) != len(p["unknown"]):
                problems.append(f"{tag}: 分からないことの一部を出さない（引用・目的・句の長さ）")
            missing = [m for m in p["missing_must"] if _belongs(m, p["ref"])]
            if len(missing) != len(p["missing_must"]):
                problems.append(f"{tag}: 欠けている要素の一部を出さない（ほかの目的の要素）")
            if p["status"] == "open" and not missing:
                p["status"] = "explained"                 # 欠けが無い open は explained と同じ（印を合わせるだけ）
            if p["status"] in SHOWN_STATUSES:             # open も explained と同じに確かめる（指示書73 §2-3）
                why = []
                if p["kind"] == "補完" and elements and all(e["relation"] == "R4" for e in elements):
                    why.append("補完の目的で R4 だけ")
                if not elements:
                    why.append("確かめを通った組が無い")
                if p["meaning"] is None:
                    why.append("① が無い")
                else:
                    why += _check_meaning(p["meaning"], kept, p, pipeline=pipe)
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
    """表示するか（仕様 §4-1・§12-2・§15-8）: pending は出さない。どちらの向きにも explained か open が無ければ出さない。
    deleted は印だけ出す。"""
    if card["status"] in HIDDEN_STATUSES:
        return False
    if card["status"] == "deleted":
        return True
    return any(p["status"] in SHOWN_STATUSES for p in card["purposes"] + card["reverse_purposes"])


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


def _is_open(p):
    """open と同じに扱う目的か（v0.7 §15-1）: 確かめ済みの組があり、欠かせない要素に覆われていないものがある。
    生成の open（指示書73 順3）と、「explained で missing_must がある」目的（取り込み分のカード）の両方。
    check() の後のカードに使う（explained・open の目的は、組と ① が確かめを通っている）。"""
    return p["status"] in SHOWN_STATUSES and bool(p["elements"]) and bool(p["missing_must"])


def _purpose_state(p):
    """目的ごとの状態（v0.7 §15-1）。出さない目的は None。
    all＝explained で、分からないこと（unknown・missing_must）が無い／part＝explained で分からないことがある（open を含む）／
    unknown＝explained でなく（not_covered）、分からないことがある。"""
    if p["status"] == "pending":
        return None
    has_must = any(e["must"] for e in p["elements"]) or bool(p["missing_must"])
    if not p["elements"] and not has_must:
        return None                                     # 欠かせない要素が無く、組も無い目的は出さない（仕様 §4-2）
    facts = bool(p["unknown"] or p["missing_must"])
    if p["status"] in SHOWN_STATUSES:
        return "part" if facts else "all"
    return "unknown" if facts else None


def _fact_items(p, o, src):
    """「文からは分からないこと」の行（v0.7 §15-3。事実だけ）。欠かせない要素（missing_must）を先に、頭に印を付ける。
    句は、分からないことの句があればそれ、無ければ必要の文（引用の原文）。同じ要素は 1 回だけ。"""
    unk = {u["need_ref"]: u["need_quote"] for u in p["unknown"]}
    items = []
    for m in p["missing_must"]:
        q = unk.get(m) or src.get(m) or _elem_key(m)
        items.append(MUST_PREFIX + FACT_TEXT.format(quote=_e(q), who=o))
    for u in p["unknown"]:
        if u["need_ref"] not in p["missing_must"]:
            items.append(FACT_TEXT.format(quote=_e(u["need_quote"]), who=o))
    return items


def _ask_quote(p, src):
    """問いの句（v0.7 §15-4 の 3・4）。対象は missing_must のうち、必要の並び順（引用の並び）で最初の 1 つで、
    必要像の要素に実在するもの。句は、同じ要素の unknown の句（check() で原文の連続した部分・40 字以内と確かめ済み）、
    無ければ必要の文の全文が 40 字以内のときだけ全文。どちらも無ければ None（問いを出さない）。"""
    order = {ref: i for i, ref in enumerate(src)}
    must = sorted((m for m in p["missing_must"] if m in src), key=order.get)
    if not must:
        return None
    m = must[0]
    q = next((u["need_quote"] for u in p["unknown"] if u["need_ref"] == m), None)
    if q is None and len(src[m]) <= QUOTE_MAX:
        q = src[m]
    return q


def _ask_target(purposes, src):
    """カード 1 枚に 1 つまで（v0.7 §15-4 の 1・2・5）: 本体の向きで、プロフィールの順で最初の open の目的だけに問いを出す。
    戻り値: (目的の参照, 句)。その目的に句が無ければ、問いは出さない（次の目的に回さない）。"""
    first = next((p for p in sorted(purposes, key=lambda x: x["order"]) if _is_open(p)), None)
    if first is None:
        return None, None
    q = _ask_quote(first, src)
    return (first["ref"], q) if q else (None, None)


def _rank(p):
    """目的の並びの段（v0.8 §16-2）: 0＝explained（欠かせない要素がすべて覆われた）／1＝open（いまは explained で
    missing_must がある目的を含む）／2＝それ以外（not_covered など）。check() の後のカードに使う。"""
    if p["status"] in SHOWN_STATUSES:
        return 1 if _is_open(p) else 0
    return 2


def _ordered(purposes):
    """目的の並び（v0.8 §16-2）: 状態の順（explained → open → not_covered）。同じ状態の中はプロフィールの順。
    目的の番号（目的1・目的2）は変えない。人の順位ではなく、一人のカードの中の目的の並び。"""
    return sorted(purposes, key=lambda x: (_rank(x), x["order"]))


def _purpose_html(p, n, o, src, reverse, has_purposes=True, ask=None):
    state = _purpose_state(p)
    if state is None:
        return ""
    if reverse and not has_purposes:                    # 相手が v4（目的が無い）: 求めている相手の文を見出しにする（仕様 §4-6）
        head = f'<h4 class="ex-ph">■ {n}が求めている相手『{_e(p["title"])}』について</h4>'
    else:
        head = f'<h4 class="ex-ph">■ {n}の目的{p["number"]}「{_e(p["title"])}」について</h4>'
    body, basis = [], []
    if p["status"] in SHOWN_STATUSES:
        marks = f'<sup class="ex-cite">{",".join(str(c + 1) for c in p["meaning"]["cites"])}</sup>'
        body.append(f'<p class="ex-meaning">{_sym(p["meaning"]["text"], n, o)}{marks}</p>')
        rows = []
        for i, e in sorted(enumerate(p["elements"]), key=lambda x: (not x[1]["must"], x[0])):   # 欠かせない要素を先に
            name, basis_text = RELATIONS[e["relation"]]
            kind = "欠かせない要素" if e["must"] else "あれば嬉しい要素"
            rows.append(
                f'<li class="ex-el" data-rel="{e["relation"]}"><div class="ex-k">{"●" if e["must"] else "○"} '
                f'{kind}（{_e(_elem_key(e["need_ref"]))}）<sup class="ex-cite">{i + 1}</sup></div>'
                f'<div class="ex-pair">{n}：{_quote(e["need_quote"], e["need_ref"], src)}</div>'
                f'<div class="ex-pair">{o}：{_quote(e["offer_quote"], e["offer_ref"], src)}</div>'
                f'<div class="ex-rel">［{_e(name)}］← {_e(basis_text)}</div></li>')
        basis.append(f'<div class="ex-sub">── {BASIS_HEAD} ──</div><ul class="ex-els">' + "".join(rows) + "</ul>")
    body.append(f'<p class="ex-state">{STATE_TEXT[state]}</p>')
    items = _fact_items(p, o, src)
    if items:
        basis.append(f'<div class="ex-sub">── {FACT_HEAD} ──</div><ul class="ex-unk">'
                     + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
    if ask and not reverse:                             # 問いは本体の向きだけ（⑥ には出さない）。事実の節の後ろに別の節で
        basis.append(f'<div class="ex-sub">── {ASK_HEAD} ──</div><ul class="ex-ask">'
                     f"<li>{ASK_TEXT.format(quote=_e(ask))}</li></ul>")
    # 根拠・分からないこと・問いは畳む（1 回の操作で開く。JS が無くても開ける）。画面3 も閉じて出す（v0.8 §16-3）
    body.append(f'<details class="ex-basis"><summary>{BASIS_SUMMARY}</summary>{"".join(basis)}</details>')
    cls = "ex-purpose ex-rev" if reverse else "ex-purpose"
    return (f'<div class="{cls}" data-ref="{_e(p["ref"])}" data-status="{_e(p["status"])}" data-state="{state}" '
            f'data-number="{int(p["number"])}">{head}{"".join(body)}</div>')


def _summary_html(purposes):
    """要旨（v0.7 §15-2）: 見る人の目的のうち、つながる意味がある目的（all・part）だけを書く 1 行。
    「分からない」の目的・出さない目的・逆の向きは入れない。書く目的が無ければ要旨を出さない。"""
    meaning = [f'目的{p["number"]}' for p in _ordered(purposes) if _purpose_state(p) in ("all", "part")]
    if not meaning:
        return ""
    return f'<p class="ex-summary">{V}の{SUMMARY_MEANING.format(list="・".join(meaning))}</p>'


def render_symbolic(card, names=()):
    """確かめを通った部分だけの HTML（呼び方は記号 ⟦V⟧・⟦T⟧ のまま）。表示しないときは None。

    画面2 で保存するのはこの HTML（を見る人に合わせて置き換えたもの）。画面3 は、この HTML を置き換えるだけで描く。"""
    checked, _ = check(card, names=names)
    if not showable(checked):
        return None
    if checked["status"] == "deleted":
        return f'<section class="ex-card" data-status="deleted"><p class="ex-deleted">{DELETED_TEXT}</p></section>'
    src = {c["ref"]: c["text"] for c in checked["citations"]}
    # 本体の向き（見る人の目的の側）と逆の向き（⑥）を印で囲む。画面3 は、保存したこの HTML を印で切り分けて並べ直す
    # （v0.8 §16-4。文は保存したものをそのまま使う）。印は HTML の注記なので、画面には出ない
    parts = [MAIN_OPEN, _summary_html(checked["purposes"])]      # 先頭の 1 行（要旨。v0.5 §1）
    if checked["status"] == "stale" and checked.get("stale"):
        changed = "・".join(_ref_label(r) for r in checked["stale"]["changed_refs"]) or "—"
        parts.append(f'<p class="ex-stale">承認時の説明（{_e(checked["stale"]["approved_at"])}）。'
                     f'現在のプロフィールと違う欄: {changed}</p>')
    # 目的は状態の順（explained → open → not_covered。同じ状態の中はプロフィールの順。v0.8 §16-2）。
    # 問いはカード 1 枚に 1 つまで（v0.7 §15-4）
    ask_ref, ask_quote = _ask_target(checked["purposes"], src)
    for p in _ordered(checked["purposes"]):
        parts.append(_purpose_html(p, V, T, src, reverse=False, ask=ask_quote if p["ref"] == ask_ref else None))
    parts.append(MAIN_CLOSE)
    rev = [h for h in (_purpose_html(p, T, V, src, reverse=True, has_purposes=checked["target_has_purposes"])
                       for p in _ordered(checked["reverse_purposes"])) if h]
    if rev:
        head = f"{T}から見た{V}" if checked["target_has_purposes"] else f"{T}が求めている相手"
        parts.append(REV_OPEN.format(kind="p" if checked["target_has_purposes"] else "s")
                     + f'<div class="ex-sub ex-revh">── {head} ──</div>' + "".join(rev) + REV_CLOSE)
    for s in checked.get("supplements") or []:
        who = V if s["by"] == "viewer" else T
        parts.append(f'<p class="ex-supp">{who}の補足（{_e(s["at"])}）：「{_e(s["text"])}」</p>')
    parts.append(f'<p class="ex-foot">{FOOTER_TEXT}</p>' + SUPPLEMENT_BUTTON)
    return (f'<section class="ex-card" data-schema="{SCHEMA_VERSION}" data-render="{RENDER_VERSION}" '
            f'data-status="{_e(checked["status"])}">' + "".join(x for x in parts if x) + "</section>")


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


_DIV_RE = re.compile(r"<div\b|</div>")
_STATUS_RE = re.compile(r'^<div [^>]*data-status="([^"]*)"')
_REV_NUM_RE = re.compile(r'<h4 class="ex-ph">■ ⟦T⟧の目的([0-9]+)「')


def _top_nodes(inner):
    """section の中身を、いちばん外側の要素（div・p・button・注記）の切れ端に分ける。分けられなければ ValueError。
    利用者の文はすべてエスケープしてあるので、切れ端の中に生の「<」は無い（タグの数え方が崩れない）。"""
    nodes, i = [], 0
    while i < len(inner):
        if inner.startswith("<!--", i):
            j = inner.index("-->", i) + 3
        elif inner.startswith("<div", i):
            depth, j = 0, None
            for m in _DIV_RE.finditer(inner, i):
                depth += 1 if m.group(0) == "<div" else -1
                if depth == 0:
                    j = m.end()
                    break
            if j is None:
                raise ValueError("div が閉じていない")
        else:
            m = re.match(r"<(p|button)\b", inner[i:])
            if not m:
                raise ValueError("知らない要素")
            j = inner.index(f"</{m.group(1)}>", i) + len(m.group(1)) + 3
        nodes.append(inner[i:j])
        i = j
    return nodes


def _node_rank(node):
    """保存した目的の枠の並びの段（v0.8 §16-2。どの版の形でも読める印だけを見る）:
    data-status が explained で、欠かせない要素の欠け（「欠かせない要素：「」の行。v0.5 以降）が無い → 0／
    explained で欠けがある（open と同じ）→ 1／それ以外 → 2。v0.4 の形は explained の目的に欠けの行を出さないので、0 になる。"""
    m = _STATUS_RE.match(node)
    if not m or m.group(1) not in SHOWN_STATUSES:
        return 2
    return 1 if (m.group(1) == "open" or MUST_PREFIX + "「" in node) else 0


def incoming_layout(symbolic_html):
    """画面3（申請を受けた人の詳細の画面）: 受けた人の目的の側（逆の向き）を先に。side_layout の first="rev"。"""
    return side_layout(symbolic_html, first="rev")


def side_layout(symbolic_html, first="rev"):
    """二つの側の並び（v0.8 §16-4・指示書74 追補1・報告74 の確認 7）。申請者が申請の時に読んだ表示（保存した記号のままの
    HTML）を、いちばん外側の要素ごとに切り分けて並べ直す。v0.4・v0.5・v0.7・v0.8 のどの形でも同じやり方。

    - first="rev": 受けた人の目的の側（逆の向き）を先に（画面3・受けた人が見る承認の後の表示）。
      first="main": 申請者の目的の側（本体）を先に（申請者が見る承認の後の表示）。それぞれの中で目的を状態の順に（同じ段の中は保存した順＝
      プロフィールの順）
    - 文は保存した HTML の切れ端をそのまま使う（作り直さない・モデルを呼ばない）。根拠は保存したまま（閉じた形）
    - 足すのは、決まった型の見出し（どちらの側か）と、受けた人の側の要旨（保存した目的の状態と見出しの番号から、要旨の型で組む。
      組めなければ出さない）だけ。⑥ の見出し（「── ⟦T⟧から見た⟦V⟧ ──」）は、側の見出しに置き換える
    - 知らない形で切り分けられなければ None（呼ぶ側が、保存した形のまま出す）"""
    if not symbolic_html or not symbolic_html.startswith("<section ") or not symbolic_html.endswith("</section>"):
        return None
    head_end = symbolic_html.index(">") + 1
    try:
        nodes = _top_nodes(symbolic_html[head_end:-len("</section>")])
    except ValueError:
        return None
    pre, main, rev, tail, kind = [], [], [], [], "p"
    for n in nodes:
        if n.startswith("<!--"):
            continue                                            # v0.8 の印（画面には出ない）
        if n.startswith('<p class="ex-summary"') or n.startswith('<p class="ex-stale"'):
            if main or rev or tail:
                return None
            pre.append(n)
        elif n.startswith('<div class="ex-purpose ex-rev"'):
            rev.append(n)
        elif n.startswith('<div class="ex-purpose"'):
            if rev:
                return None
            main.append(n)
        elif n.startswith('<div class="ex-sub ex-revh">'):
            kind = "s" if "が求めている相手 ──" in n else "p"
        elif n.startswith(('<p class="ex-supp"', '<p class="ex-foot"', "<button")):
            tail.append(n)
        else:
            return None                                         # 知らない要素（並べ直さない）
        if tail and not n.startswith(('<p class="ex-supp"', '<p class="ex-foot"', "<button")):
            return None                                         # 注記の後に目的が来る形は知らない
    if not main and not rev:
        return None
    main.sort(key=_node_rank)                                   # 安定な並べ替え（同じ段の中は保存した順）
    rev.sort(key=_node_rank)
    layout = "incoming" if first == "rev" else "applicant"
    opening = symbolic_html[:head_end].replace("<section ", f'<section data-layout="{layout}" ', 1)
    side_rev = side_main = ""
    if rev:
        side_rev = f'<div class="ex-side ex-side-rev"><div class="ex-side-h">{SIDE_HEAD_REV[kind].format(t=T, v=V)}</div>'
        nums = [m.group(1) for m in (_REV_NUM_RE.search(n) for n in rev if _node_rank(n) < 2) if m] if kind == "p" else []
        if nums:
            side_rev += f'<p class="ex-summary">{T}の{SUMMARY_MEANING.format(list="・".join(f"目的{n}" for n in nums))}</p>'
        side_rev += "".join(rev) + "</div>"
    if main or pre:
        side_main = (f'<div class="ex-side ex-side-main"><div class="ex-side-h">{SIDE_HEAD_MAIN.format(t=T, v=V)}</div>'
                     + "".join(pre + main) + "</div>")
    sides = [side_rev, side_main] if first == "rev" else [side_main, side_rev]
    return "".join([opening] + sides + tail) + "</section>"


def incoming_html(symbolic_html, names):
    """画面3 に出す HTML（受けた人が実際に見る表示。承認のときは、これをその人の表示として残す）。
    incoming_layout で並べ直す。根拠は保存したまま閉じた形（v0.8 §16-3。指示書74 追補1 で前に保存した申請にも当てる）。
    並べ直せない形は、保存した形のまま（名前の置き換えだけ。根拠は開かない）。"""
    lay = incoming_layout(symbolic_html)
    return personalize(symbolic_html if lay is None else lay, "recipient", names)


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
    status = "explained" if any(p["status"] in SHOWN_STATUSES for p in purposes + reverse) else "not_covered"
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
        for p in _ordered(purposes):                               # 状態の順（v0.8 §16-2）。open の ① も出す（問いは出さない）
            if p["status"] not in SHOWN_STATUSES:
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
