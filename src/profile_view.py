#!/usr/bin/env python3
"""
PoX 変換層 — seeker（非公開・原本）→ profile_view（公開・表示用）

・純粋関数。LLM 不使用。副作用なし。
・入力キーが欠けても例外を投げない。
・_meta / 生テキスト / _ 始まりキーは出力に含まない。
"""
import re


# ── ブラケット抽出 ────────────────────────────────────────────

def _extract_bracket(text: str) -> str:
    """『…』または「…」の中身を返す。なければ strip した原文。"""
    m = re.search(r'[『「](.+?)[』」]', text)
    return m.group(1) if m else text.strip()


def _parse_trajectory_item(item) -> dict:
    """
    「目標『X』→ 次に必要『Y』」を {from, to} に分解。
    → が無い場合・非文字列は {from: 原文, to: ""} にフォールバック（情報を捨てない）。
    """
    if not isinstance(item, str):
        return {"from": str(item), "to": ""}
    if "→" in item:
        left, right = item.split("→", 1)
        return {"from": _extract_bracket(left), "to": _extract_bracket(right)}
    return {"from": item.strip(), "to": ""}


# ── 公開変換 ─────────────────────────────────────────────────

def build_profile_view(seeker: dict) -> dict:
    """
    seeker dict → profile_view dict（公開用）。

    出力キー:
      headline    : 要約文
      pursuing    : 意志
      needs       : 求めている
      offering    : 能力
      phase_badge : {value, unconfirmed}
      keys        : attention候補[]
      trajectory  : [{from, to}]

    除外: 生テキスト / _meta / _phase_* / _ 始まりキー全て
    """
    if not isinstance(seeker, dict):
        seeker = {}

    # supporting_material: 無ければ seeker トップレベルを二段目として探す
    sm = seeker.get("supporting_material")
    if not isinstance(sm, dict):
        sm = seeker

    headline = sm.get("要約文") or seeker.get("要約文") or ""

    keys_raw = sm.get("attention候補") or seeker.get("attention候補") or []
    keys = list(keys_raw) if isinstance(keys_raw, list) else []

    traj_raw = sm.get("系列素材") or seeker.get("系列素材") or []
    trajectory = [
        _parse_trajectory_item(item)
        for item in (traj_raw if isinstance(traj_raw, list) else [])
    ]

    phase_val    = seeker.get("フェーズ") or ""
    phase_status = seeker.get("_phase_status") or ""

    return {
        "headline": str(headline) if headline else "",
        "pursuing": str(seeker.get("意志")       or ""),
        "needs":    str(seeker.get("求めている")  or ""),
        "offering": str(seeker.get("能力")        or ""),
        "phase_badge": {
            "value":       str(phase_val) if phase_val else "",
            "unconfirmed": phase_status == "candidate_unconfirmed",
        },
        "keys":       keys,
        "trajectory": trajectory,
    }
