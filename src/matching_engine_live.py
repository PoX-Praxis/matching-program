#!/usr/bin/env python3
"""
PoX マッチング・エンジン 最小実装（LLM代行版）
==============================================
verify.py が「正解注入」していた意味類似度を、実際の Claude 呼び出しに差し替えた版。
仕様 v0.5 5.5節「仮・少人数なら意味比較は LLM 代行で足る」を実装する。

層の分担（仕様3章の三層）:
  - LLM が判断: (2)attention重み付け / (3.5)翻訳層 / (4)二チャネルの類似度判定
  - コードが固定: 遷移テーブル参照 / 二チャネル合算 / ランキング / 根拠整形

入力規格（demoのAの形をそのまま最小規格に採用）:
  seeker = {意志, 求めている, 能力, フェーズ}  + want("実装"/"同志") で合算重みを選ぶ
  candidates = [{id, profile}, ...]
"""
import os, json, urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-opus-4-8"

# === 遷移テーブル（仕様7章 + 7.2の集計結果の代わりに最小ハードコード） ===
# 本番は github_transition_builder.py が吐く確率テーブルに差し替える。
TABLE = [
    {"phase": "mvp", "follower_bucket": "fol:100-1k", "repo_bucket": "repo:>30", "n": 40, "p_positive_change": 0.85},
    {"phase": "mvp", "follower_bucket": "fol:10-100", "repo_bucket": "repo:5-30", "n": 30, "p_positive_change": 0.55},
]


def _call_claude(system, user, max_tokens=1024):
    """Claude を1回呼ぶ。JSONのみを返すよう促し、パースして返す。"""
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


# === (3)系列予測層 + (3.5)翻訳層 ===
def predict_needed_roles(phase):
    """遷移テーブルを引き、(3.5)でバケットを役割記述へ言語化（LLM）。"""
    rows = sorted([r for r in TABLE if r["phase"] == phase], key=lambda r: -r["p_positive_change"])
    if not rows:
        return []
    buckets = [{"follower": r["follower_bucket"], "repo": r["repo_bucket"], "w": r["p_positive_change"]} for r in rows]
    sys_p = "あなたはPoXマッチングエンジンの翻訳層(3.5)。統計の特徴バケットを、人の自己記述と同じ意味空間に乗る『役割記述』に言語化する。"
    usr_p = (
        "次の特徴バケットそれぞれを、簡潔な日本語の役割記述に翻訳して。\n"
        f"{json.dumps(buckets, ensure_ascii=False)}\n"
        '出力はJSONのみ: [{"role":"役割記述","w":重み}, ...]'
    )
    return _call_claude(sys_p, usr_p)


# === (4)照合層: 二チャネルの類似度を LLM に判定させる ===
def score_channels(seeker, candidate, needed_roles):
    """相補(必要役割⇔候補の実像) と 類似(意志⇔候補) を 0..1 で LLM 判定。"""
    sys_p = (
        "あなたはPoXマッチングエンジンの照合層(4)。二つのチャネルを独立に0..1で採点する。"
        "相補=『求める人の必要な役割』と『候補の実像』の非対称マッチ。"
        "類似=『求める人の意志』と『候補の意志』の対称的な近さ。意味で判断し、文字面に引っ張られない。"
    )
    usr_p = (
        f"求める人の意志: {seeker['意志']}\n"
        f"求める人の必要な役割(複数可): {json.dumps([r['role'] for r in needed_roles], ensure_ascii=False)}\n"
        f"候補ID: {candidate['id']}\n候補プロフィール: {candidate['profile']}\n\n"
        '各役割について相補スコアを出し最大を採用。出力はJSONのみ: '
        '{"comp":0..1,"comp_via":"決め手の役割","sim":0..1}'
    )
    return _call_claude(sys_p, usr_p)


def match(seeker, candidates, w_comp, w_sim):
    needed = predict_needed_roles(seeker["フェーズ"])
    out = []
    for c in candidates:
        ch = score_channels(seeker, c, needed)
        score = w_comp * ch["comp"] + w_sim * ch["sim"]
        out.append({"id": c["id"], "score": round(score, 3),
                    "comp": ch["comp"], "comp_via": ch.get("comp_via"), "sim": ch["sim"]})
    out.sort(key=lambda r: -r["score"])
    return out, needed


if __name__ == "__main__":
    # demoのA（フェーズ②MVP）をそのまま入力規格として使う
    seeker = {
        "意志": "地方の小さな農家が作った野菜を都市の飲食店に直接売れる仕組みを作りたい",
        "求めている": "アプリを形にする実装技術",
        "能力": "農業の現場・販路の理解",
        "フェーズ": "mvp",
    }
    candidates = [
        {"id": "B", "profile": "複数のSaaSでアプリを実装。社会課題に技術で関わりたい"},
        {"id": "C", "profile": "都市と地方をつなぐ食のサービスを構想中。マーケが得意"},
        {"id": "D", "profile": "飲食店向け仕入れSaaSを2年運営。現場に詳しい"},
        {"id": "E", "profile": "料理写真のインスタ運用"},
    ]

    print("=== (3)+(3.5) Aの必要像 ===")
    needed = predict_needed_roles(seeker["フェーズ"])
    for n in needed:
        print(f"  「{n['role']}」 重み{n['w']}")

    for title, wc, ws in [("シナリオ1: 実装がほしい(相補0.7/類似0.3)", 0.7, 0.3),
                          ("シナリオ2: 同志がほしい(相補0.3/類似0.7)", 0.3, 0.7)]:
        res, _ = match(seeker, candidates, wc, ws)
        print(f"\n### {title}")
        print(f"{'候補':<5}{'総合':<8}{'相補':<8}{'類似':<8}相補の決め手")
        for r in res:
            print(f"{r['id']:<5}{r['score']:<8}{r['comp']:<8}{r['sim']:<8}{r['comp_via']}")
