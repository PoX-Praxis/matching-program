#!/usr/bin/env python3
"""
マッチングエンジン 結線検証版
=============================
目的を「結線ロジックの正しさ」に限定する。意味類似度(本番では既製embeddingが出す値)を
既知の正解として外部注入し、(3)→(4)の配線・二チャネル合算・ランキング・根拠が
仕様通りに動くかだけを検証する。embeddingの品質はここでは検証対象外(差し替え前提)。
"""
import json

# === 意味類似度を「本番embeddingが出すであろう値」として正解注入 ===
# sim(役割記述/要素テキスト, 相手テキスト) を 0..1 で与える。
# これは本番では sentence embedding のcosineが自動で出す部分。
SIM = {
    # 相補チャネル: Aの必要役割「実装力のあるエンジニア」と各候補の実像の意味的近さ
    ("実装力のあるエンジニア","B"): 0.90,  # Bは実装者 → 高い
    ("実装力のあるエンジニア","C"): 0.15,  # Cは構想家 → 低い
    ("実装力のあるエンジニア","D"): 0.05,  # Dは無関係
    # 類似チャネル: Aの強調(構想・繋ぐ意志)と各候補の意味的近さ
    ("Aの構想","B"): 0.30,                 # Bは構想は薄い
    ("Aの構想","C"): 0.85,                 # Cは同じ構想 → 高い
    ("Aの構想","D"): 0.05,
}

# === 遷移テーブル(②MVPでは実装力が来ると噛み合う) ===
TABLE=[
 {"phase":"②MVP","follower_bucket":"fol:100-1k","repo_bucket":"repo:>30","n":40,"p_positive_change":0.85},
 {"phase":"②MVP","follower_bucket":"fol:10-100","repo_bucket":"repo:5-30","n":30,"p_positive_change":0.55},
]
FEATURE_TO_ROLE={("fol:100-1k","repo:>30"):"実装力のあるエンジニア",
                 ("fol:10-100","repo:5-30"):"そこそこの開発者"}

def predict_needed(phase):
    """(3)系列予測層: テーブルを引き、必要役割を確率重み付きで返す。"""
    rows=sorted([r for r in TABLE if r["phase"]==phase],
                key=lambda r:-r["p_positive_change"])
    return [{"role":FEATURE_TO_ROLE.get((r["follower_bucket"],r["repo_bucket"]),"?"),
             "w":r["p_positive_change"],"n":r["n"]} for r in rows]

def match(phase, candidates, w_comp=0.6, w_sim=0.4):
    """(4)照合層: 二チャネル合算。"""
    needs=predict_needed(phase)
    out=[]
    for B in candidates:
        # 相補: 必要役割それぞれと相手の近さ × 役割の確率重み、最大を採用
        comp=0.0; via=None
        for nd in needs:
            s=SIM.get((nd["role"],B),0.0)*nd["w"]
            if s>comp: comp=s; via=nd["role"]
        # 類似: Aの構想と相手の近さ
        sim=SIM.get(("Aの構想",B),0.0)
        score=w_comp*comp+w_sim*sim
        out.append({"cand":B,"score":round(score,3),
                    "comp":round(comp,3),"comp_via":via,"sim":round(sim,3)})
    out.sort(key=lambda r:-r["score"])
    return out,needs

def show(title, w_comp, w_sim):
    res,needs=match("②MVP",["B","C","D"],w_comp,w_sim)
    print(f"\n### {title}  (相補{w_comp} / 類似{w_sim})")
    print(f"{'候補':<5}{'総合':<8}{'相補':<8}{'類似':<8}相補の決め手")
    for r in res:
        print(f"{r['cand']:<5}{r['score']:<8}{r['comp']:<8}{r['sim']:<8}{r['comp_via']}")
    return res

print("=== (3)系列予測層の出力: Aの必要像 ===")
for n in predict_needed("②MVP"):
    print(f"  「{n['role']}」 重み{n['w']} (n={n['n']})")

# シナリオ1: Aは実装力が欲しい(相補重視) → Bが勝つべき
r1=show("シナリオ1: 相補重視(Aは実装者が欲しい)", 0.6, 0.4)
# シナリオ2: 同じ夢の仲間が欲しい(類似重視) → Cが勝つべき
r2=show("シナリオ2: 類似重視(Aは同志が欲しい)", 0.3, 0.7)

print("\n=== 結線検証の判定 ===")
ok1 = r1[0]["cand"]=="B"
ok2 = r2[0]["cand"]=="C"
print(f"  シナリオ1で B(実装者)が1位: {'✓ 成功' if ok1 else '✗ 失敗'}  → 相補チャネルが遷移テーブル経由で機能")
print(f"  シナリオ2で C(同志)が1位 : {'✓ 成功' if ok2 else '✗ 失敗'}  → 類似チャネルが機能、合算重みで切替可能")
print(f"  D(無関係)が両シナリオで最下位: {'✓' if r1[-1]['cand']=='D' and r2[-1]['cand']=='D' else '✗'}")
print(f"\n  総合判定: {'✓ (3)→(4)の結線は仕様通り動作' if ok1 and ok2 else '✗ 要見直し'}")
