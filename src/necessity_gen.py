"""
PoX v4 必要像生成層（②生成LLM / 仕様書 D章 / Step 4）

登録/更新時に seeker ごと一度だけ実行。意志と現状の差分（＋地の文）から
必要像テキストと、ゲート/結合/チャネル重みの素材を数値化して出力する。

責務（I章）:
  - 必要像・数値（s,u,p,α,β,γ）は ② が所有。① / プラットフォームは持たない。
  - 入力は redacted のみ（防御的に redact）。"未取得" は本文素材にしない（明示はする）。
  - 本人申告（求めている）は検証・補強材料。申告を超える必要も拾う（方針イ）。

実行系統:
  - generator_fn 注入時（テスト/差し替え）: それを使う。
  - ANTHROPIC_API_KEY あり: 実 Claude。
  - どちらも無し: _demo_generate（API キー無しでも動く・非意味的）。
"""
import os, json, hashlib, urllib.request, pathlib

from embedding_service import state_concat
from embedding_config import MODEL_TAG
from pii_redaction import redact_text, redact_supporting
from match_config import (
    GAMMA_MAX, P_SHARPNESS_DEFAULT, ALPHA_DEFAULT, BETA_DEFAULT,
)

PROMPT_VERSION = "necessity-v4.1"
MODEL = "claude-opus-4-8"
API_URL = "https://api.anthropic.com/v1/messages"

# ── 標準プロンプトの外部化（B-2: 二重管理を防ぎ、①作成の素材にする）──────────────
# spec/necessity_gen_prompt_v1.md の SYSTEM / USER 区画を唯一のソースとして読む。
_PROMPT_FILE = pathlib.Path(__file__).parent.parent / "spec" / "necessity_gen_prompt_v1.md"


def _extract_section(text, name):
    start = f"<!-- {name}_START -->"
    end = f"<!-- {name}_END -->"
    i = text.index(start) + len(start)
    j = text.index(end)
    return text[i:j].strip()


_PROMPT_TEXT = _PROMPT_FILE.read_text(encoding="utf-8")
_SYSTEM_PROMPT_TEXT = _extract_section(_PROMPT_TEXT, "SYSTEM_PROMPT")
_USER_PROMPT_TEMPLATE = _extract_section(_PROMPT_TEXT, "USER_PROMPT_TEMPLATE")

# ② が出力する必須キー
_REQUIRED = ("necessity_text", "gate_s", "gate_u", "p_sharpness",
             "alpha", "beta", "evidence_span")


# ── ゲート γ（D-2: 不確実性ベース・フロア禁止）────────────────────────────────
def compute_gamma(s, u, gamma_max=GAMMA_MAX):
    """
    γ = gamma_max * min(1, s + u(1-s))。
    性質: f(s≈0,u≈0)→0（多数派保護） / ∂γ/∂u>0（迷いで取りこぼし保護）。
    禁止: s に固定フロアを足すこと（トピック相関で幻の意志要求を捏造）。
          バイアスは u に載せる（2-3 / Sakana D2）。
    """
    s = max(0.0, min(1.0, float(s)))
    u = max(0.0, min(1.0, float(u)))
    base = s
    uncertainty_boost = u * (1.0 - s)
    return gamma_max * min(1.0, base + uncertainty_boost)


# ── 再生成 hash（B-3 / Sakana #6）─────────────────────────────────────────────
def _supporting_for_generation(profile):
    """② 生成入力に使う地の文＋素材（redacted）。"未取得"はそのまま（明示）。"""
    sup = profile.get("supporting_redacted") or profile.get("supporting_material") or {}
    sup = redact_supporting(sup) if isinstance(sup, dict) else {}
    keys = ("生テキスト", "要約文", "求めている", "意志要求の素材",
            "連言選言の素材", "チャネル重み素材", "系列素材")
    return {k: sup.get(k, "未取得") for k in keys}


def compute_src_hash(profile, prompt_version=PROMPT_VERSION, model_tag=MODEL_TAG):
    """
    再生成判定の入力 hash（B-3）。will + state連結 + 生成用supporting +
    prompt_version + model_tag のいずれが変わっても hash が変わる（#6 の穴を塞ぐ）。
    """
    will = redact_text(profile.get("will_text") or "")
    state = state_concat({
        k: redact_text(profile.get(k))
        for k in ("state_have", "state_can_type", "state_bound", "state_unsorted")
    })
    payload = {
        "will": will,
        "state": state,
        "supporting": _supporting_for_generation(profile),
        "prompt_version": prompt_version,
        "model_tag": model_tag,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def needs_regeneration(profile, stored_hash, prompt_version=PROMPT_VERSION,
                       model_tag=MODEL_TAG):
    """現在の src_input_hash が保存値と不一致なら True（②再実行が必要）。"""
    return compute_src_hash(profile, prompt_version, model_tag) != stored_hash


# ── 入力組み立て（redacted / D-1）─────────────────────────────────────────────
def gather_inputs(profile):
    """② 生成 LLM に渡す入力一式（redacted）。"""
    return {
        "will": redact_text(profile.get("will_text") or ""),
        "state": state_concat({
            k: redact_text(profile.get(k))
            for k in ("state_have", "state_can_type", "state_bound", "state_unsorted")
        }),
        "supporting": _supporting_for_generation(profile),
    }


# ── 実 Claude 呼び出し ────────────────────────────────────────────────────────
def _system_prompt():
    """spec/necessity_gen_prompt_v1.md の SYSTEM 区画（外部化・単一ソース）。"""
    return _SYSTEM_PROMPT_TEXT


def _user_prompt(inputs):
    """USER テンプレート（外部化）に意志/現状/素材を差し込む。"""
    return (_USER_PROMPT_TEMPLATE
            .replace("{{WILL}}", inputs["will"])
            .replace("{{STATE}}", inputs["state"])
            .replace("{{SUPPORTING}}", json.dumps(inputs["supporting"], ensure_ascii=False)))


def _call_claude(inputs):
    body = {"model": MODEL, "max_tokens": 1024, "system": _system_prompt(),
            "messages": [{"role": "user", "content": _user_prompt(inputs)}]}
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json",
                 "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    text = "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


# ── demo 生成（API キー無しでも動く・非意味的）────────────────────────────────
def _demo_generate(inputs):
    """
    LLM 無しのフォールバック。必要像は申告（求めている）優先、無ければ差分テンプレ。
    数値は素材の有無から素朴に決める（意味判断はしない）。実運用は LLM 必須。
    """
    sup = inputs["supporting"]
    want = sup.get("求めている")
    if want and want != "未取得":
        necessity = want
    else:
        necessity = f"{inputs['will']} を前に進めるために、いまの制約を補える存在"
    # 素材が "未取得" 中心なら不確実性を高く（D-3 既定の素朴版）
    material_keys = ("意志要求の素材", "連言選言の素材", "チャネル重み素材")
    missing = sum(1 for k in material_keys if sup.get(k, "未取得") == "未取得")
    u = round(missing / len(material_keys), 3)
    iyou = sup.get("意志要求の素材", "未取得")
    s = 0.2 if iyou == "未取得" else 0.5
    return {
        "necessity_text": necessity,
        "gate_s": s, "gate_u": u,
        "p_sharpness": P_SHARPNESS_DEFAULT,
        "alpha": ALPHA_DEFAULT, "beta": BETA_DEFAULT,
        "evidence_span": "" if iyou == "未取得" else iyou,
    }


# ── 出力検証 ──────────────────────────────────────────────────────────────────
def _validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("② 出力が dict ではありません")
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        raise ValueError(f"② 出力に必須キー欠落: {missing}")
    out = {
        "necessity_text": str(raw["necessity_text"]),
        "gate_s": max(0.0, min(1.0, float(raw["gate_s"]))),
        "gate_u": max(0.0, min(1.0, float(raw["gate_u"]))),
        "p_sharpness": float(raw["p_sharpness"]),
        "alpha": max(0.0, float(raw["alpha"])),
        "beta": max(0.0, float(raw["beta"])),
        "evidence_span": str(raw.get("evidence_span", "")),
    }
    if not out["necessity_text"].strip():
        raise ValueError("necessity_text が空")
    return out


# ── 公開 API ──────────────────────────────────────────────────────────────────
def generate_necessity(profile, *, generator_fn=None, gamma_max=GAMMA_MAX,
                       model_tag=MODEL_TAG):
    """
    必要像と連続パラメータを生成し、derived_necessity 行に対応する dict を返す（D章）。

    profile        : profiles_v4 フラット dict（will_text / state_* / supporting_redacted）。
    generator_fn   : テスト/差し替え用。inputs(dict) -> ② 出力 raw(dict)。
    戻り値         : necessity_text / gate_s,u / gamma / p_sharpness / alpha / beta /
                     evidence_span / src_input_hash / generator_prompt_version /
                     generator_model_tag / is_generated / model_tag。
    """
    inputs = gather_inputs(profile)
    if generator_fn is not None:
        raw = generator_fn(inputs); gen_tag = "injected"
    elif os.environ.get("ANTHROPIC_API_KEY"):
        raw = _call_claude(inputs); gen_tag = MODEL          # 例: claude-opus-4-8
    else:
        raw = _demo_generate(inputs); gen_tag = "demo-fallback"

    parsed = _validate(raw)
    gamma = compute_gamma(parsed["gate_s"], parsed["gate_u"], gamma_max)
    src_hash = compute_src_hash(profile, PROMPT_VERSION, model_tag)

    return {
        "necessity_text": parsed["necessity_text"],
        "gate_s": parsed["gate_s"],
        "gate_u": parsed["gate_u"],
        "gamma": gamma,
        "p_sharpness": parsed["p_sharpness"],
        "alpha": parsed["alpha"],
        "beta": parsed["beta"],
        "evidence_span": parsed["evidence_span"],
        "src_input_hash": src_hash,
        "generator_prompt_version": PROMPT_VERSION,
        # 経路/生成モデルの識別（束2a: 従来意味を維持。user-supplied 経路は別関数で "user-supplied"）
        "generator_model_tag": gen_tag,
        "generator_name": None,   # 利用者申告AI名は user-supplied 経路のみ
        "is_generated": True,
        "model_tag": model_tag,
    }


# ── 各自AI生成の必要像を「受領」する（判断B の正式ルート・サーバー生成しない）─────
def build_user_necessity(profile, supplied, *, gamma_max=GAMMA_MAX, model_tag=MODEL_TAG):
    """
    各自の AI が構造化と同時に生成した必要像フィールド（supplied）を受領し、
    derived_necessity 行に対応する dict を返す。**各自AIの出力を無検証で信頼しない**:
    _validate で必須キー検証・範囲クランプ（gate_s/u∈[0,1], alpha/beta≥0）を必ず通す。
    γ は compute_gamma で算出（supplied の gamma は使わない = フロア捏造・改竄を防ぐ）。

    supplied 必須キー: necessity_text, gate_s, gate_u, p_sharpness, alpha, beta, evidence_span
    supplied 任意キー: generator（利用者が使ったAI名）
    generator_model_tag = "user-supplied"（経路識別）。
    generator_name      = supplied["generator"]（欠損時 "user-supplied(unknown)"）。
    """
    parsed = _validate(supplied)  # 無検証で信頼しない: 必須キー＋範囲クランプ
    gamma = compute_gamma(parsed["gate_s"], parsed["gate_u"], gamma_max)
    src_hash = compute_src_hash(profile, PROMPT_VERSION, model_tag)
    gen_name = str(supplied.get("generator") or "").strip() or "user-supplied(unknown)"
    return {
        "necessity_text": parsed["necessity_text"],
        "gate_s": parsed["gate_s"],
        "gate_u": parsed["gate_u"],
        "gamma": gamma,
        "p_sharpness": parsed["p_sharpness"],
        "alpha": parsed["alpha"],
        "beta": parsed["beta"],
        "evidence_span": parsed["evidence_span"],
        "src_input_hash": src_hash,
        "generator_prompt_version": PROMPT_VERSION,
        "generator_model_tag": "user-supplied",
        "generator_name": gen_name,
        "is_generated": False,   # 本人側AI生成＝サーバー生成物ではない
        "model_tag": model_tag,
    }
