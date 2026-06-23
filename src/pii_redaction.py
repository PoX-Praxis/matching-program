"""
PoX v4 構造PII redaction（仕様書 B-1 MVP / Step 2）

MVP = 構造PII の軽量 redaction。正規表現で機械検出できるものだけをマスクする:
  メール / 電話番号 / URL / 郵便番号 / カード様数字列 / @ハンドル
将来拡張 = 意味的 PII（文脈で人物特定しうる固有名詞等）は範囲外（status で区別）。

設計原則（I章）:
  - raw テキストを embedding/②生成に直接渡さない。redacted のみ使う。
  - will/state 系テキストも、② 生成・embedding に渡す前に同じ redaction を通す
    （redact_text を C/D 層が共用できるよう公開する）。
  - 欠落 "未取得" は捏造で埋めない。redaction はマスクのみで、値の創作はしない。
"""
import re

# redaction 段階（B-1）。supporting_redacted 生成後にこの status を立てる。
STATUS_PENDING         = "pending"
STATUS_STRUCTURAL_DONE = "structural_done"
STATUS_SEMANTIC_DONE   = "semantic_done"   # 将来拡張（意味的 PII）。MVP では使わない。

# マスク置換トークン（種類別。元が何だったか分かる形で残す＝②/UI の手掛かり）
MASK = {
    "email":  "[EMAIL]",
    "phone":  "[PHONE]",
    "url":    "[URL]",
    "zip":    "[ZIP]",
    "card":   "[CARD]",
    "handle": "[HANDLE]",
}

# ── 検出ルール（H-6: 網羅性は実機で詰める。ここに集約し散らさない）────────────
# 順序が重要:
#   1. URL/email を先に処理（内部の数字列が phone/card に誤マッチするのを防ぐ）
#   2. zip(NNN-NNNN) は phone より先（短い固定形なので、電話の断片を誤って残さない）。
#      ただし両側を数字/ハイフンで挟まれた断片は除外し、電話番号の一部を ZIP 化しない。
#   3. card(13-16桁) → phone の順。
_RULES = [
    # URL（http(s)/www）。クエリ文字列も含めて飲み込む。
    ("url",   re.compile(r"\b(?:https?://|www\.)[^\s<>()]+", re.IGNORECASE)),
    # メール
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # 郵便番号: 日本形式 123-4567 / 〒付き。前後を数字・ハイフンで挟まれない真の郵便番号のみ。
    ("zip",   re.compile(r"(?<![\d\-])〒?\s?\d{3}-\d{4}(?![\d\-])")),
    # カード様数字列: 13〜16 桁を 4 桁ごとに空白/ハイフン区切り、または連続
    ("card",  re.compile(r"\b(?:\d[ \-]?){13,16}\b")),
    # 電話番号: 日本の 0始まり / 国際 +、区切り(空白/ハイフン)許容、10〜11 桁相当
    ("phone", re.compile(
        r"(?<!\d)(?:\+?\d{1,3}[ \-]?)?(?:0\d{1,4}[ \-]?)\d{1,4}[ \-]?\d{3,4}(?!\d)")),
    # @ハンドル（先頭が @ の英数字列）。メール除去後なので安全。
    ("handle", re.compile(r"(?<![\w@])@[A-Za-z0-9_]{2,}\b")),
]


def redact_text(text):
    """
    1 つの文字列から構造PII をマスクして返す。
    None / 非文字列 / "未取得" はそのまま返す（捏造・改変しない）。

    C 層（embedding 入力）・D 層（② 生成入力）からも呼べる公開関数。
    """
    if not isinstance(text, str) or text == "未取得" or text == "":
        return text
    out = text
    for kind, pat in _RULES:
        out = pat.sub(MASK[kind], out)
    return out


def _redact_json(value):
    """JSONB 値（dict / list / str / その他）を再帰的に redaction。"""
    if isinstance(value, dict):
        return {k: _redact_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_json(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value  # 数値・bool・None はそのまま


def redact_supporting(supporting_raw):
    """
    supporting_material（JSONB: 生テキスト[]/要約文/求めている/各素材/系列素材[]）を
    丸ごと redaction した dict を返す。"未取得" は保持される（redact_text が素通し）。
    """
    if not isinstance(supporting_raw, dict):
        return supporting_raw
    return _redact_json(supporting_raw)


def redact_profile_fields(profile):
    """
    profiles_v4 の本源二軸テキスト（will/state 4スロット）と supporting を
    まとめて redaction した dict を返す。元 dict は変更しない（非破壊）。

    入力 profile は ①v4 出力（seeker.意志 / seeker.現状.* / supporting_material）
    でも、profiles_v4 のフラット列名（will_text 等）でも受けられる。
    照合・② 生成に渡す前の唯一の入口として使う（I章 raw 直接渡し禁止）。
    """
    out = dict(profile)

    # フラット列名（profiles_v4 形式）
    for col in ("will_text", "state_have", "state_can_type",
                "state_bound", "state_unsorted"):
        if col in out:
            out[col] = redact_text(out[col])

    # ネスト形式（①v4 出力 seeker.意志 / seeker.現状）
    seeker = out.get("seeker")
    if isinstance(seeker, dict):
        seeker = dict(seeker)
        if "意志" in seeker:
            seeker["意志"] = redact_text(seeker["意志"])
        genjou = seeker.get("現状")
        if isinstance(genjou, dict):
            seeker["現状"] = {k: redact_text(v) for k, v in genjou.items()}
        out["seeker"] = seeker

    # supporting（両形式のキー名に対応）
    for key in ("supporting_raw", "supporting_material"):
        if key in out:
            out[key] = redact_supporting(out[key])

    return out


def redact_for_storage(supporting_raw):
    """
    登録/更新フローの実利用口（B-1）。
    supporting_raw を受け取り (redacted_dict, status) を返す。
    呼び出し側は profiles_v4.supporting_redacted と pii_redaction_status に保存する。
    """
    redacted = redact_supporting(supporting_raw)
    return redacted, STATUS_STRUCTURAL_DONE
