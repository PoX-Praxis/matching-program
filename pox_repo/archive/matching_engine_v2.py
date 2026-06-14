#!/usr/bin/env python3
"""
マッチングエンジン v0.2 —— v0で炙り出た「依存関係」を反映した修正版
==================================================================
v0の発見: 相補チャネルが機能するには、遷移テーブルの特徴バケットと
人の自己記述が「同じ意味空間」に乗らねばならない。文字ハッシュでは不可能。

修正点(=設計仕様として確定すべき事項):
  【修正1】遷移テーブルの特徴バケットを、必ず「意味のある役割記述」へ
          言語化してからベクトル化する (特徴→自然言語の翻訳テーブルを噛ませる)。
          本番ではこの翻訳もLLMが行える。
  【修正2】疑似embeddingを、文字ngramでなく「語の重なり(意味の粗い近似)」に変更し、
          意味照合の最小再現にする。本番は既製embeddingに差し替え。
"""
import json, math, argparse, re
from collections import defaultdict

# ---- (1) ベクトル化層: 語ベースの粗い意味ベクトル (本番は既製embeddingに差替) ----
def tokenize(t):
    return set(re.findall(r"[ぁ-んァ-ン一-龥a-zA-Z]+", (t or "").lower()))

# 意味の橋渡し用シソーラス(最小)。本番はembeddingが吸収する語彙のゆれを、ここで明示。
SYN = {
    "実装":{"実装","コード","エンジニア","開発","プログラム","バックエンド","作れる","技術"},
    "構想":{"構想","事業","アイデア","ユーザー","リサーチ","理解","設計","企画"},
    "繋ぐ":{"繋ぐ","つなぐ","接続","マッチング","プラットフォーム","スキル"},
}
def expand(tokens):
    out=set(tokens)
    for canon,group in SYN.items():
        if tokens & group: out.add(canon)
    return out

def sim(text_a, text_b):
    a=expand(tokenize(text_a)); b=expand(tokenize(text_b))
    if not a or not b: return 0.0
    return len(a&b)/math.sqrt(len(a)*len(b))   # コサイン的な重なり

# ---- 【修正1】特徴バケット → 役割記述 の翻訳テーブル ----
# 遷移テーブルの抽象的なバケットを、人の自己記述と同じ意味空間に乗せる。
# 本番ではLLMが「この特徴を持つ人はどんな役割か」を言語化して生成する。
FEATURE_TO_ROLE = {
    ("fol:100-1k","repo:>30"): "多くのリポジトリで開発してきた実装力のあるエンジニア",
    ("fol:10-100","repo:5-30"): "ある程度の開発経験を持つ作れる人",
    ("fol:<10","repo:<5"): "経験の浅い人",
    ("fol:>1k","repo:>30"): "著名で実績豊富な技術リーダー",
}

class Person:
    def __init__(self, name, phase, elements):
        self.name=name; self.phase=phase
        self.elements=elements  # [(label, text, weight)]
    def text_weighted(self):
        return [(l,t,w) for l,t,w in self.elements]

def predict_needed_roles(phase, table, top_k=3):
    rows=[r for r in table if r["phase"]==phase]
    rows=sorted(rows, key=lambda r:-r.get("p_positive_change",0))[:top_k]
    needs=[]
    for r in rows:
        role=FEATURE_TO_ROLE.get((r.get("follower_bucket"),r.get("repo_bucket")),
                                 f"{r.get('follower_bucket')} {r.get('repo_bucket')}")
        needs.append({"role_text":role,"weight":r.get("p_positive_change",0),
                      "n":r.get("n")})
    return needs

def match(A, candidates, table, w_complement=0.6, w_similar=0.4):
    needs=predict_needed_roles(A.phase, table)
    results=[]
    for B in candidates:
        B_text=" ".join(t for _,t,_ in B.elements)
        # 相補: Aの必要役割(複数) ⇔ Bの実像。重み付き最大マッチ
        comp=0.0; comp_hit=None
        for need in needs:
            s=sim(need["role_text"], B_text)*need["weight"]
            if s>comp: comp=s; comp_hit=need["role_text"]
        # 類似: Aの強調要素 ⇔ Bの要素。重み付き
        A_text=" ".join(t for _,t,w in A.elements for _ in range(int(w*10)))
        simi=sim(A_text, B_text)
        # 要素対応の根拠
        pairs=[]
        for la,ta,wa in A.elements:
            best=max(B.elements, key=lambda eb: sim(ta,eb[1]))
            pairs.append((la,best[0],round(sim(ta,best[1]),3)))
        pairs.sort(key=lambda x:-x[2])
        score=w_complement*comp+w_similar*simi
        results.append({"candidate":B.name,"score":round(score,3),
                        "complement":round(comp,3),"comp_via":comp_hit,
                        "similar":round(simi,3),"pairs":pairs[:2]})
    results.sort(key=lambda r:-r["score"])
    return results, needs

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--table",default=None); a=ap.parse_args()
    if a.table:
        table=json.load(open(a.table))["transition_table"]
    else:
        table=[
            {"phase":"②MVP/機能拡張","follower_bucket":"fol:100-1k","repo_bucket":"repo:>30","n":40,"p_positive_change":0.85},
            {"phase":"②MVP/機能拡張","follower_bucket":"fol:10-100","repo_bucket":"repo:5-30","n":30,"p_positive_change":0.6},
            {"phase":"②MVP/機能拡張","follower_bucket":"fol:<10","repo_bucket":"repo:<5","n":25,"p_positive_change":0.2},
        ]
    A=Person("A(創業者)","②MVP/機能拡張",[
        ("意志","世界中の個人をスキルで繋ぐプラットフォームを作りたい",1.0),
        ("求めている","プロトタイプを実装してくれるエンジニアが欲しい",0.9),
        ("自分の能力","事業構想とユーザー理解は得意だがコードは書けない",0.4),
    ])
    cands=[
        Person("B(実装者)","②MVP/機能拡張",[
            ("能力","多くのリポジトリでバックエンドを開発してきた実装力",1.0),
            ("求めている","面白い構想に技術で参加したい",0.7)]),
        Person("C(同種の構想家)","②MVP/機能拡張",[
            ("意志","人と人をスキルで繋ぐサービスを構想している",1.0),
            ("能力","事業構想とユーザーリサーチが得意",0.8)]),
        Person("D(無関係)","②MVP/機能拡張",[
            ("意志","料理レシピの動画を作るのが好き",1.0)]),
    ]
    results,needs=match(A,cands,table)
    print("### (3)系列予測層: Aの必要像(遷移テーブル→役割記述に翻訳)")
    for n in needs:
        print(f"  - 「{n['role_text']}」(重み{n['weight']}, n={n['n']})")
    print(f"\n### (4)照合層: ランキング (相補0.6 + 類似0.4)")
    print(f"{'候補':<16}{'総合':<7}{'相補':<7}{'類似':<7}")
    for r in results:
        print(f"{r['candidate']:<16}{r['score']:<7}{r['complement']:<7}{r['similar']:<7}")
        print(f"     相補の決め手: {r['comp_via']}")
    print("\n検証: Aが今ほしいのは相補(実装力)。B(実装者)が総合1位なら結線成功。")

if __name__=="__main__":
    main()
