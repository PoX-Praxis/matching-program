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
import os, json, hashlib, urllib.request

from embedding_service import state_concat
from embedding_config import MODEL_TAG
from pii_redaction import redact_text, redact_supporting
from match_config import (
    GAMMA_MAX, P_SHARPNESS_DEFAULT, ALPHA_DEFAULT, BETA_DEFAULT,
)

PROMPT_VERSION = "necessity-v4.1"
MODEL = "claude-opus-4-8"
API_URL = "https://api.anthropic.com/v1/messages"

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
    return (
        "あなたは PoX 接続システムの②必要像生成層。"
        "本人の『意志（どこへ向かうか）』と『現状（いま何を持ち、何に縛られるか）』の差分から、"
        "その差分を埋める存在の記述＝『必要像』を生成する。"
        "必要像は候補者の現状記述と同じ意味空間に乗る自然文にする（翻訳層3.5）。"
        "価値判断（良い/悪い）はしない。本人申告（求めている）は検証・補強材料であり、"
        "申告を超える／外れた必要（本人が言語化できていない必要）も積極的に拾う（方針イ）。"
        "さらに以下を素材から数値化する:"
        " gate_s=相手に意志・姿勢まで求める強さ(0..1。現状能力だけなら低)、"
        " gate_u=不確実性(0..1。素材が薄い/未取得/迷いがあるなら高)、"
        " p_sharpness=結合鋭さ(連言『すべて必須』が強いほど負、選言『どれかでよい』なら0寄り、既定0)、"
        " alpha=共鳴(同じ志)重み, beta=補完(不足を埋める)重み。"
        " evidence_span=意志要求と判断した根拠の引用。"
        "出力は純粋な JSON のみ。"
    )


def _user_prompt(inputs):
    return (
        f"意志:\n{inputs['will']}\n\n"
        f"現状:\n{inputs['state']}\n\n"
        f"地の文・素材(JSON):\n{json.dumps(inputs['supporting'], ensure_ascii=False)}\n\n"
        '出力JSON: {"necessity_text":"...","gate_s":0..1,"gate_u":0..1,'
        '"p_sharpness":数値,"alpha":数値,"beta":数値,"evidence_span":"..."}'
    )


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
        raw = generator_fn(inputs)
    elif os.environ.get("ANTHROPIC_API_KEY"):
        raw = _call_claude(inputs)
    else:
        raw = _demo_generate(inputs)

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
        "generator_model_tag": model_tag,
        "is_generated": True,
        "model_tag": model_tag,
    }
