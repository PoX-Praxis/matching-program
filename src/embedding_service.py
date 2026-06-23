"""
PoX v4 embedding サービス（仕様書 C章 / Step 3）

embed(text, role) が full(FULL_DIM) + short(SHORT_DIM) の L2 正規化ベクトルを返す。
prefix は embedding_config に外出し（H-1）。MRL 短次元は先頭 SHORT_DIM を再正規化（C-1/C-4）。

バックエンド:
  - 'stub'  : 決定論的・非意味的。Qwen3 ホスト確定までの代替（POX_EMBED_BACKEND 既定）。
  - 'qwen3' : セルフホスト常駐推論（QWEN3_ENDPOINT）。ホスト確定後に有効化。

I章の遵守:
  - raw を embedding に直接渡さない → build_vectors は入力テキストを防御的に redact する。
  - "未取得" は embedding 入力から除外（#11）。state_concat / clean_slot で落とす。
"""
import math, hashlib, struct, json, os

from embedding_config import (
    PREFIX, FULL_DIM, SHORT_DIM, EPS, MODEL_TAG, BACKEND, QWEN3_ENDPOINT,
)
from pii_redaction import redact_text


# ── ベクトル基本演算 ──────────────────────────────────────────────────────────
def l2_normalize(vec):
    """L2 正規化。ゼロベクトルは（割り算回避のため）そのまま返す。"""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm < EPS:
        return list(vec)
    return [x / norm for x in vec]


def cosine(a, b):
    """L2 正規化済みベクトル前提の内積＝コサイン。長さ不一致はエラー。"""
    if len(a) != len(b):
        raise ValueError(f"次元不一致: {len(a)} vs {len(b)}")
    return sum(x * y for x, y in zip(a, b))


def guard(cos):
    """
    定義域ガード（C-3 / Sakana #4）。冪平均は x>0 前提。
    cos を [-1,1] にクリップし (1+cos)/2 で (0,1] へ写像。EPS で下限を 0 にしない。
    """
    cos = max(-1.0, min(1.0, cos))
    return max(EPS, (1.0 + cos) / 2.0)


# ── バックエンド: stub（決定論的・非意味的）──────────────────────────────────
def _stub_encode(text):
    """
    text から決定論的に FULL_DIM 次元の擬似ベクトルを作る（意味は持たない）。
    同一 text → 同一ベクトル。Qwen3 ホスト確定までのプレースホルダ。
    照合の数式検証（Step 5）は合成ベクトル注入で行うため、ここに意味は不要。
    """
    out = []
    counter = 0
    while len(out) < FULL_DIM:
        h = hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest()
        for i in range(0, len(h), 4):
            if len(out) >= FULL_DIM:
                break
            val = struct.unpack("<I", h[i:i + 4])[0]
            out.append((val / 2**32) * 2.0 - 1.0)   # [-1, 1)
        counter += 1
    return out


# ── バックエンド: qwen3（セルフホスト常駐推論。ホスト確定後に有効化）──────────
def _qwen3_encode(text):
    if not QWEN3_ENDPOINT:
        raise RuntimeError(
            "POX_EMBED_BACKEND=qwen3 だが POX_QWEN3_ENDPOINT が未設定です。"
            "Qwen3 常駐推論サービスの URL を設定してください（仕様 C章/A-3）。"
        )
    import urllib.request
    req = urllib.request.Request(
        QWEN3_ENDPOINT,
        data=json.dumps({"text": text, "model_tag": MODEL_TAG}).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    vec = data["embedding"]
    if len(vec) != FULL_DIM:
        raise RuntimeError(f"Qwen3 出力次元 {len(vec)} が FULL_DIM={FULL_DIM} と不一致")
    return vec


def _encode(text):
    if BACKEND == "qwen3":
        return _qwen3_encode(text)
    return _stub_encode(text)


# ── 公開 API: embed ───────────────────────────────────────────────────────────
def embed(text, role):
    """
    text を role の prefix 付きでエンコードし (full, short) を返す（C-1）。
    role: 'symmetric' | 'query' | 'passage'。
    short = full の先頭 SHORT_DIM を再正規化（MRL）。
    """
    if role not in PREFIX:
        raise ValueError(f"未知の role: {role}（{list(PREFIX)} のいずれか）")
    full_text = PREFIX[role] + (text or "")
    full = l2_normalize(_encode(full_text))
    short = l2_normalize(full[:SHORT_DIM])
    return full, short


# ── 登録時の 4 ベクトル生成（C-2）─────────────────────────────────────────────
def clean_slot(x):
    """"未取得"・空・None を None に落とす（embedding 入力から除外。#11）。"""
    if not x or x == "未取得":
        return None
    return x


def state_concat(profile):
    """
    現状 4 スロットを自然文連結（"未取得"は混ぜない / #11）。
    profile は profiles_v4 フラット形式の dict（state_have / state_can_type / ...）。
    軸は意志/現状の 2 軸。現状内は割らず一本に連結する（将来の第二レイヤーで分割余地）。
    """
    parts = []
    if clean_slot(profile.get("state_have")):
        parts.append("持っているもの: " + profile["state_have"])
    if clean_slot(profile.get("state_can_type")):
        parts.append("動き方の型: " + profile["state_can_type"])
    if clean_slot(profile.get("state_bound")):
        parts.append("縛られているもの: " + profile["state_bound"])
    if clean_slot(profile.get("state_unsorted")):
        parts.append("未分類の現状: " + profile["state_unsorted"])
    return "\n".join(parts)


def build_vectors(profile_redacted, necessity_text):
    """
    登録/更新フローで 4 ベクトル（full+short の 8 本）を生成し、
    profile_vectors の列に対応する dict を返す（C-2）。

    入力:
      profile_redacted : profiles_v4 フラット dict（redacted 済みを期待）。
                         防御的に各テキストを redact_text に通す（I章: raw 直渡し禁止）。
      necessity_text   : ② が生成した必要像テキスト（Step 4 の出力）。

    意志は対称用(symmetric)と passage 用で別エンコード（混同禁止）。
    """
    will = redact_text(profile_redacted.get("will_text") or "")
    # state_concat 用に各スロットを防御 redact した dict を作る
    safe_profile = {
        k: redact_text(profile_redacted.get(k))
        for k in ("state_have", "state_can_type", "state_bound", "state_unsorted")
    }
    state_text = state_concat(safe_profile)
    necessity = redact_text(necessity_text or "")

    wsym, wsym256 = embed(will, "symmetric")        # a チャネル
    wpas, wpas256 = embed(will, "passage")          # c チャネル（passage。a と別ベクトル）
    spas, spas256 = embed(state_text, "passage")    # b チャネル（相補・現状）
    nq,   nq256   = embed(necessity, "query")       # query 側

    return {
        "model_tag":       MODEL_TAG,
        "will_symmetric":  wsym,  "will_sym_256":    wsym256,
        "will_passage":    wpas,  "will_pas_256":    wpas256,
        "state_passage":   spas,  "state_pas_256":   spas256,
        "necessity_query": nq,    "necessity_q_256": nq256,
    }
