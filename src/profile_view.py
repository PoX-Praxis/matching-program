#!/usr/bin/env python3
"""
PoX 変換層 — 登録テキスト → seeker（非公開・原本）→ profile_view（公開・表示用）

v4: 二軸（意志/現状）スキーマ対応。v3.1 は後方互換で保持。

登録の変換経路:
    貼り付けテキスト
      ↓ parse_registration_text()   ← ``` 除去＋JSONパース
      ↓ normalize_to_seeker()        ← 構造の揺れを seeker 標準形へ正規化
    seeker（標準形）
      ↓ build_profile_view()         ← 表示用に変換（v3/v4 分岐）
    profile_view
      ↓ apply_overrides()            ← view_overrides を重ねて返す（閲覧時）
"""
import re
import json


# ── ① 登録テキストのパース ──────────────────────────────────────

def strip_code_fence(text: str) -> str:
    """先頭末尾の ```（```json 含む）を除去し、最初の { から最後の } までを返す。"""
    if not isinstance(text, str):
        text = str(text or "")
    # Normalize iOS smart quotes to ASCII before JSON parse
    text = (text
            .replace('\u201c', '"').replace('\u201d', '"')
            .replace('\u2018', "'").replace('\u2019', "'"))
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text.strip()


def parse_registration_text(text: str) -> dict:
    """登録テキストを dict にする。失敗時は ValueError。"""
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

def _clean_supporting_material(sm) -> dict:
    """v3.1 supporting_material を標準4キー・型保証で整える。"""
    if not isinstance(sm, dict):
        sm = {}
    raw_texts = sm.get("生テキスト")
    attention = sm.get("attention候補")
    series = sm.get("系列素材")
    return {
        "生テキスト":    list(raw_texts) if isinstance(raw_texts, list) else [],
        "要約文":        str(sm.get("要約文") or ""),
        "attention候補": list(attention) if isinstance(attention, list) else [],
        "系列素材":      list(series) if isinstance(series, list) else [],
    }


def _clean_supporting_material_v4(sm) -> dict:
    """v4 supporting_material を型保証で整える。既存素材の欠落は '未取得'、
    v4.3 追加の表示用キー（背景/意志_なぜ/意志_どこへ/経験/一行紹介）の欠落は空文字。

    注意: ここに足したキーは表示・保存のみ。② 生成入力は necessity_gen の
    _supporting_for_generation（7キー固定）だけを見るため、src_input_hash は変わらない。
    """
    if not isinstance(sm, dict):
        sm = {}

    def _s(k):
        v = sm.get(k)
        return str(v) if v else "未取得"

    def _s_opt(k):  # 表示用の新キー: 欠落は空文字（"未取得" を入れない）
        v = sm.get(k)
        return str(v) if v else ""

    raw_texts = sm.get("生テキスト")
    series = sm.get("系列素材")
    return {
        "生テキスト":      list(raw_texts) if isinstance(raw_texts, list) else [],
        "要約文":          str(sm.get("要約文") or ""),
        "求めている":      _s("求めている"),
        "意志要求の素材":  _s("意志要求の素材"),
        "連言選言の素材":  _s("連言選言の素材"),
        "チャネル重み素材": _s("チャネル重み素材"),
        "系列素材":        list(series) if isinstance(series, list) else [],
        # ── v4.3 表示用（②生成・ベクトル・照合には不関与）──
        "背景":       _s_opt("背景"),
        "意志_なぜ":   _s_opt("意志_なぜ"),
        "意志_どこへ": _s_opt("意志_どこへ"),
        "経験":       _s_opt("経験"),
        "一行紹介":    _s_opt("一行紹介"),
    }


def _is_v4_raw(raw: dict) -> bool:
    """登録JSONがv4かどうかを判定する。"""
    if raw.get("schema_version") == "v4":
        return True
    inner = raw.get("seeker")
    if isinstance(inner, dict) and isinstance(inner.get("現状"), dict):
        return True
    meta = raw.get("_meta") or {}
    if meta.get("schema_version") == "v4":
        return True
    return False


def normalize_to_seeker(raw: dict) -> dict:
    """
    登録JSONの揺れを吸収して seeker 標準形へ。

    v4: 現状を dict のまま保持。supporting_material は7キー。
    v3: 後方互換。意志/求めている/能力/フェーズ。
    """
    if not isinstance(raw, dict):
        raw = {}

    inner = raw.get("seeker")
    if isinstance(inner, dict):
        seeker = dict(inner)
        sm = raw.get("supporting_material")
        handle = raw.get("id") or inner.get("id")
        meta = raw.get("_meta") or inner.get("_meta")
    else:
        seeker = {k: v for k, v in raw.items()
                  if k not in ("supporting_material", "_meta")}
        sm = raw.get("supporting_material")
        handle = raw.get("id")
        meta = raw.get("_meta")

    if not isinstance(meta, dict):
        meta = {}

    if _is_v4_raw(raw):
        seeker["supporting_material"] = _clean_supporting_material_v4(sm)
        meta.setdefault("schema_version", "v4")
        if not isinstance(seeker.get("現状"), dict):
            seeker["現状"] = {
                "持っているもの": "", "できること_型": "",
                "縛られているもの": "", "未分類": "",
            }
    else:
        seeker["supporting_material"] = _clean_supporting_material(sm)
        meta.setdefault("schema_version", "v3")
        seeker.setdefault("_phase_status", "candidate_unconfirmed")

    seeker["_meta"] = meta
    if handle:
        seeker["id"] = handle

    return seeker


# ── ③ profile_view への変換 ───────────────────────────────────

def _extract_bracket(text: str) -> str:
    """『…』または「…」の中身を返す。なければ strip した原文。"""
    m = re.search(r'[『「](.+?)[』」]', text)
    return m.group(1) if m else text.strip()


def _parse_trajectory_item(item) -> dict:
    """「目標『X』→ 次に必要『Y』」を {from, to} に分解。"""
    if not isinstance(item, str):
        return {"from": str(item), "to": ""}
    if "→" in item:
        left, right = item.split("→", 1)
        return {"from": _extract_bracket(left), "to": _extract_bracket(right)}
    return {"from": item.strip(), "to": ""}


def _first_sentence(text: str) -> str:
    if not text:
        return ""
    return re.split(r"[。\n]", text, 1)[0].strip()


def build_profile_view(seeker: dict) -> dict:
    """
    seeker dict → profile_view dict（公開用）。v3/v4 両対応。

    v4 出力キー:
      schema_version / headline / pursuing / state_have / state_can_type /
      state_bound / state_unsorted / seeking / keys / trajectory

    v3 出力キー（後方互換）:
      schema_version / headline / pursuing / needs / offering /
      phase_badge / keys / trajectory
    """
    if not isinstance(seeker, dict):
        seeker = {}

    meta = seeker.get("_meta") or {}
    schema_ver = meta.get("schema_version", "v3")
    sm = seeker.get("supporting_material")
    if not isinstance(sm, dict):
        sm = seeker

    will = str(seeker.get("意志") or "")
    headline = sm.get("要約文") or seeker.get("要約文") or ""
    if not headline:
        headline = _first_sentence(will)

    jotai = seeker.get("現状")
    is_v4 = schema_ver == "v4" or isinstance(jotai, dict)

    if is_v4:
        if not isinstance(jotai, dict):
            jotai = {}
        series_raw = sm.get("系列素材") or []
        trajectory = [_parse_trajectory_item(item)
                      for item in (series_raw if isinstance(series_raw, list) else [])]
        keys_raw = sm.get("attention候補") or seeker.get("attention候補") or []
        keys = list(keys_raw) if isinstance(keys_raw, list) else []
        return {
            "schema_version": "v4",
            "headline":       str(headline),
            "pursuing":       will,
            "state_have":     str(jotai.get("持っているもの") or ""),
            "state_can_type": str(jotai.get("できること_型") or ""),
            "state_bound":    str(jotai.get("縛られているもの") or ""),
            "state_unsorted": str(jotai.get("未分類") or ""),
            "seeking":        str(sm.get("求めている") or ""),
            "keys":           keys,
            "trajectory":     trajectory,
            # ── v4.3 表示用（欠落は空文字。テンプレ側はフォールバックで凌ぐ）──
            "background":  str(sm.get("背景") or "") or str(headline),
            "will_where":  str(sm.get("意志_どこへ") or "") or will,
            "will_why":    str(sm.get("意志_なぜ") or ""),
            "will_origin": str(sm.get("経験") or ""),
            "one_liner":   str(sm.get("一行紹介") or ""),
        }
    else:
        keys_raw = sm.get("attention候補") or seeker.get("attention候補") or []
        keys = list(keys_raw) if isinstance(keys_raw, list) else []
        traj_raw = sm.get("系列素材") or seeker.get("系列素材") or []
        trajectory = [_parse_trajectory_item(item)
                      for item in (traj_raw if isinstance(traj_raw, list) else [])]
        phase_val = seeker.get("フェーズ") or ""
        phase_status = seeker.get("_phase_status") or ""
        return {
            "schema_version": "v3",
            "headline":    str(headline) if headline else "",
            "pursuing":    will,
            "needs":       str(seeker.get("求めている") or ""),
            "offering":    str(seeker.get("能力") or ""),
            "phase_badge": {
                "value":       str(phase_val) if phase_val else "",
                "unconfirmed": phase_status == "candidate_unconfirmed",
            },
            "keys":       keys,
            "trajectory": trajectory,
        }


# ── ④ 表示オーバーレイ（view_overrides を profile_view に重ねる）─

def _traj_sig(t: dict) -> str:
    return f"{t.get('from', '')}|||{t.get('to', '')}"


def apply_overrides(pv: dict, overrides: dict) -> dict:
    """
    profile_view に view_overrides を重ねた結果を返す。
    overrides は表示専用設定であり、マッチング根拠（seeker）には一切触れない。
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

    # 自由記述「本人より」（表示専用・seeker/necessity に触れない）
    free = overrides.get("free_text")
    if isinstance(free, str):
        result["free_text"] = free.strip()

    return result
