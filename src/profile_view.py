#!/usr/bin/env python3
"""
PoX 変換層 — 登録テキスト → seeker（非公開・原本）→ profile_view（公開・表示用）

・純粋関数のみ。LLM 不使用。副作用なし。
・入力キーが欠けても例外を投げない（防御的）。
・_meta / 生テキスト / _ 始まりキーは profile_view 出力に含まない。

登録の変換経路（修正指示書 v3.1 §3）:
    貼り付けテキスト
      ↓ parse_registration_text()   ← ``` 除去＋JSONパース（受け側の責任）
      ↓ normalize_to_seeker()        ← 構造の揺れを seeker 標準形へ正規化
    seeker（標準形）
      ↓ build_profile_view()         ← 表示用に変換
    profile_view
      ↓ apply_overrides()            ← view_overrides を重ねて返す（閲覧時）
"""
import re
import json


# ── ① 登録テキストのパース（``` 除去は受け側の責任） ──────────────

def strip_code_fence(text: str) -> str:
    """
    先頭末尾の ```（```json 含む）や前後の説明文を除去し、
    最初の `{` から対応する最後の `}` までを取り出して返す。
    """
    if not isinstance(text, str):
        text = str(text or "")
    # ``` で囲まれたブロックがあれば中身を優先的に拾う
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    # 最初の { から最後の } まで
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text.strip()


def parse_registration_text(text: str) -> dict:
    """
    登録テキストを dict にする。失敗時は ValueError（直し方を示すメッセージ付き）。
    """
    cleaned = strip_code_fence(text)
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        raise ValueError(
            "JSONとして読めませんでした。AIの出力の { から } までを貼り付けてください。"
        )
    if not isinstance(data, dict):
        raise ValueError(
            "JSONとして読めませんでした。AIの出力の { から } までを貼り付けてください。"
        )
    return data


# ── ② seeker 標準形への正規化 ─────────────────────────────────

def _empty_supporting_material() -> dict:
    return {"生テキスト": [], "要約文": "", "attention候補": [], "系列素材": []}


def _clean_supporting_material(sm) -> dict:
    """supporting_material を標準4キー・型保証で整える（欠損は空で補う）。"""
    if not isinstance(sm, dict):
        sm = {}
    raw_texts = sm.get("生テキスト")
    attention = sm.get("attention候補")
    series = sm.get("系列素材")
    return {
        "生テキスト":   list(raw_texts) if isinstance(raw_texts, list) else [],
        "要約文":       str(sm.get("要約文") or ""),
        "attention候補": list(attention) if isinstance(attention, list) else [],
        "系列素材":     list(series) if isinstance(series, list) else [],
    }


def normalize_to_seeker(raw: dict) -> dict:
    """
    登録JSONの揺れを吸収して seeker 標準形（フラット）へ。

    対応する入力:
      ・フル版   : raw["seeker"]（dict）と raw["supporting_material"] がある
      ・旧式簡易 : トップレベルに 意志/求めている/能力/フェーズ が直接ある
      ・記入式   : supporting_material はあるが attention候補/系列素材 が空

    返り値（標準形）: 意志/求めている/能力/フェーズ/_phase_status/_phase_candidates
                      ＋ supporting_material（4キー）＋ _meta ＋ id（ハンドル属性）
    """
    if not isinstance(raw, dict):
        raw = {}

    inner = raw.get("seeker")
    if isinstance(inner, dict):
        # フル版: seeker 配下をそのまま採用
        seeker = dict(inner)
        sm = raw.get("supporting_material")
        handle = raw.get("id") or inner.get("id")
        meta = raw.get("_meta") or inner.get("_meta")
    else:
        # 旧式簡易/記入式フラット: トップレベルを seeker とみなす
        seeker = {
            k: v for k, v in raw.items()
            if k not in ("supporting_material", "_meta")
        }
        sm = raw.get("supporting_material")
        handle = raw.get("id")
        meta = raw.get("_meta")

    seeker["supporting_material"] = _clean_supporting_material(sm)

    if handle:
        seeker["id"] = handle

    if not isinstance(meta, dict):
        meta = {}
    meta.setdefault("schema_version", "v3")
    seeker["_meta"] = meta

    if not seeker.get("_phase_status"):
        seeker["_phase_status"] = "candidate_unconfirmed"

    return seeker


# ── ③ profile_view への変換 ───────────────────────────────────

def _extract_bracket(text: str) -> str:
    """『…』または「…」の中身を返す。なければ strip した原文。"""
    m = re.search(r'[『「](.+?)[』」]', text)
    return m.group(1) if m else text.strip()


def _parse_trajectory_item(item) -> dict:
    """
    「目標『X』→ 次に必要『Y』」を {from, to} に分解。
    → が無い・非文字列は {from: 原文, to: ""} にフォールバック（情報を捨てない）。
    """
    if not isinstance(item, str):
        return {"from": str(item), "to": ""}
    if "→" in item:
        left, right = item.split("→", 1)
        return {"from": _extract_bracket(left), "to": _extract_bracket(right)}
    return {"from": item.strip(), "to": ""}


def _first_sentence(text: str) -> str:
    """文字列の先頭1文（。または改行まで）を返す。"""
    if not text:
        return ""
    return re.split(r"[。\n]", text, 1)[0].strip()


def build_profile_view(seeker: dict) -> dict:
    """
    seeker dict → profile_view dict（公開用）。

    出力キー:
      headline    : 要約文（空なら意志の先頭1文を流用＝ヘッダーを空にしない §3.3）
      pursuing    : 意志
      needs       : 求めている
      offering    : 能力
      phase_badge : {value, unconfirmed}
      keys        : attention候補[]
      trajectory  : [{from, to}]

    除外: 生テキスト / _meta / _phase_* / id / _ 始まりキー全て
    """
    if not isinstance(seeker, dict):
        seeker = {}

    sm = seeker.get("supporting_material")
    if not isinstance(sm, dict):
        sm = seeker  # フォールバック: トップレベルを二段目として探す

    will = str(seeker.get("意志") or "")

    headline = sm.get("要約文") or seeker.get("要約文") or ""
    if not headline:
        headline = _first_sentence(will)  # §3.3: 要約文が空なら意志の先頭1文

    keys_raw = sm.get("attention候補") or seeker.get("attention候補") or []
    keys = list(keys_raw) if isinstance(keys_raw, list) else []

    traj_raw = sm.get("系列素材") or seeker.get("系列素材") or []
    trajectory = [
        _parse_trajectory_item(item)
        for item in (traj_raw if isinstance(traj_raw, list) else [])
    ]

    phase_val = seeker.get("フェーズ") or ""
    phase_status = seeker.get("_phase_status") or ""

    return {
        "headline": str(headline) if headline else "",
        "pursuing": will,
        "needs":    str(seeker.get("求めている") or ""),
        "offering": str(seeker.get("能力") or ""),
        "phase_badge": {
            "value":       str(phase_val) if phase_val else "",
            "unconfirmed": phase_status == "candidate_unconfirmed",
        },
        "keys":       keys,
        "trajectory": trajectory,
    }


# ── ④ 表示オーバーレイ（view_overrides を profile_view に重ねる §5）─

def _traj_sig(t: dict) -> str:
    return f"{t.get('from', '')}|||{t.get('to', '')}"


def apply_overrides(pv: dict, overrides: dict) -> dict:
    """
    profile_view（seeker由来）に view_overrides を重ねた結果を返す。
    overrides は表示専用設定であり、マッチング根拠（seeker）には一切触れない。

    overrides 形式（いずれも任意）:
      headline   : 文字列。非空なら headline を差し替え。
      keys       : 表示する attention候補テキストの並び（順序＝表示順、含まれない＝非表示）。
      trajectory : 表示する軌跡シグネチャ "from|||to" の並び（順序＝表示順）。
    """
    if not isinstance(overrides, dict) or not overrides:
        return pv
    result = dict(pv)

    head = overrides.get("headline")
    if isinstance(head, str) and head.strip():
        result["headline"] = head.strip()

    ov_keys = overrides.get("keys")
    if isinstance(ov_keys, list):
        current = pv.get("keys", [])
        result["keys"] = [k for k in ov_keys if k in current]

    ov_traj = overrides.get("trajectory")
    if isinstance(ov_traj, list):
        by_sig = {_traj_sig(t): t for t in pv.get("trajectory", [])}
        result["trajectory"] = [by_sig[s] for s in ov_traj if s in by_sig]

    return result
