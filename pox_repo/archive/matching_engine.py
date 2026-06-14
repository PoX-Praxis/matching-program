#!/usr/bin/env python3
"""
マッチングエンジン v0  —— (3)系列予測層 → (4)照合層 の接続実証
=================================================================
仕様書v0.2 の 4.5節(二チャネル) / 5章(要素別ベクトル) / 7章(遷移テーブル) を結線する。

このファイルが実証すること:
  ・遷移テーブル P(相手特徴|フェーズ) から「Aの必要像」を生成する (=(3)系列予測層)
  ・必要像とBの実像を非対称に照合する相補チャネル (=(4)の片輪)
  ・AとBの要素を対称に照合する類似チャネル        (=(4)のもう片輪)
  ・二チャネル合算 + 要素対応の根拠出力

【重要】embedding は本番では既製モデル(多言語sentence-transformer等)を使う。
ここでは外部依存なしで結線ロジックを検証するため、軽量な疑似embedding
(文字ngramハッシュbaseのベクトル)で代用する。ロジックの正しさの検証が目的であり、
本番では (1)ベクトル化層を差し替えるだけでよい設計にしてある。
"""
import json, math, hashlib, argparse
from collections import defaultdict

# ----------------------------------------------------------------------
# (1) ベクトル化層 —— 本番では既製embeddingに差し替え。ここは疑似実装。
# ----------------------------------------------------------------------
DIM = 64
def embed(text):
    """文字3-gramをハッシュして固定次元ベクトルに。意味の近さは粗いが結線検証には十分。"""
    v = [0.0]*DIM
    t = (text or "").lower()
    grams = [t[i:i+3] for i in range(max(0,len(t)-2))] or [t]
    for g in grams:
        h = int(hashlib.md5(g.encode()).hexdigest(), 16)
        v[h % DIM] += 1.0
        v[(h//DIM) % DIM] += 0.5
    n = math.sqrt(sum(x*x for x in v)) or 1.0
    return [x/n for x in v]

def cosine(a, b):
    return sum(x*y for x,y in zip(a,b))

def weighted_sum(vecs, weights):
    """要素ベクトル群を重み付き集約 (=(2)attention層の出力形)。"""
    acc=[0.0]*DIM
    for v,w in zip(vecs,weights):
        for i in range(DIM): acc[i]+=v[i]*w
    n=math.sqrt(sum(x*x for x in acc)) or 1.0
    return [x/n for x in acc]

# ----------------------------------------------------------------------
# 人の表現: 要素別ベクトル + LLMが付けたattention重み(申告ベース)
# ----------------------------------------------------------------------
class Person:
    def __init__(self, name, phase, elements):
        """elements: [(要素ラベル, 本文, attention重み)] —— 重みは(2)でLLMが付与した想定。"""
        self.name=name
        self.phase=phase
        self.elements=elements
        self.elem_vecs=[(label, embed(text), w) for label,text,w in elements]
    def emphasized_vector(self):
        """(2)の出力: attention重みで集約した、その人の強調表現。"""
        vecs=[v for _,v,_ in self.elem_vecs]
        ws  =[w for _,_,w in self.elem_vecs]
        return weighted_sum(vecs, ws)

# ----------------------------------------------------------------------
# (3) 系列予測層 —— 遷移テーブルから「Aの必要像」を生成する
# ----------------------------------------------------------------------
def load_transition_table(path):
    with open(path) as f: return json.load(f)["transition_table"]

def predict_needed_profile(phase, table, top_k=3):
    """
    遷移テーブル P(相手特徴|フェーズ) を引き、状態変化を起こしやすい相手特徴を
    確率(p_positive_change)で重み付けして「必要像ベクトル」に合成する。
    返り値: (必要像ベクトル, 寄与した特徴の内訳[根拠用])
    """
    rows=[r for r in table if r["phase"]==phase]
    if not rows:
        return None, []
    # p_positive_change が高い特徴ほど「来ると噛み合う」 → それを必要像として重視
    rows=sorted(rows, key=lambda r:-r.get("p_positive_change",0))[:top_k]
    vecs=[]; ws=[]; rationale=[]
    for r in rows:
        # 特徴バケットを言語化してベクトル化 (本番は特徴の意味埋め込みに置換可)
        feat_text=f"{r.get('follower_bucket','')} {r.get('repo_bucket','')}"
        vecs.append(embed(feat_text))
        ws.append(r.get("p_positive_change",0))
        rationale.append({"feature":feat_text.strip(),
                          "p_positive_change":r.get("p_positive_change"),
                          "n":r.get("n")})
    if not vecs: return None, []
    return weighted_sum(vecs, ws), rationale

# ----------------------------------------------------------------------
# (4) 照合層 —— 二チャネルで突き合わせ、合算し、根拠を出す
# ----------------------------------------------------------------------
def match(A, candidates, table, w_complement=0.6, w_similar=0.4):
    needed_vec, need_rationale = predict_needed_profile(A.phase, table)
    results=[]
    A_emph = A.emphasized_vector()
    for B in candidates:
        B_emph = B.emphasized_vector()
        # --- 相補チャネル: Aの必要像 ⇔ Bの実像(非対称) ---
        complement = cosine(needed_vec, B_emph) if needed_vec else 0.0
        # --- 類似チャネル: Aの強調 ⇔ Bの強調(対称) ---
        similar = cosine(A_emph, B_emph)
        # --- 要素対応の根拠: Aの各要素とBの各要素で最も噛んだペア ---
        best_pairs=[]
        for la,va,wa in A.elem_vecs:
            best=max(B.elem_vecs, key=lambda eb: cosine(va,eb[1]))
            best_pairs.append((la, best[0], round(cosine(va,best[1]),3)))
        best_pairs.sort(key=lambda x:-x[2])
        score = w_complement*complement + w_similar*similar
        results.append({
            "candidate":B.name,
            "score":round(score,3),
            "complement":round(complement,3),
            "similar":round(similar,3),
            "top_element_pairs":best_pairs[:2],
        })
    results.sort(key=lambda r:-r["score"])
    return results, need_rationale

# ----------------------------------------------------------------------
# デモ: 遷移テーブルが無くても動くよう、ダミーテーブルを内蔵
# ----------------------------------------------------------------------
DEMO_TABLE = {"transition_table":[
    # ②MVP段階では「作れる人(repo多・実装力)」が来ると状態変化が起きやすい、という想定分布
    {"phase":"②MVP/機能拡張","follower_bucket":"fol:100-1k","repo_bucket":"repo:>30","n":40,"p_positive_change":0.85},
    {"phase":"②MVP/機能拡張","follower_bucket":"fol:10-100","repo_bucket":"repo:5-30","n":30,"p_positive_change":0.6},
    {"phase":"②MVP/機能拡張","follower_bucket":"fol:<10","repo_bucket":"repo:<5","n":25,"p_positive_change":0.2},
    {"phase":"④スケール","follower_bucket":"fol:>1k","repo_bucket":"repo:>30","n":20,"p_positive_change":0.9},
]}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--table", default=None, help="github_transition_builder.py の出力JSON。無ければ内蔵デモ表")
    args=ap.parse_args()
    if args.table:
        table=load_transition_table(args.table)
    else:
        table=DEMO_TABLE["transition_table"]

    # --- A: ②MVP段階。アイデアと構想は強いが実装力が手薄、と本人が語った想定 ---
    A = Person("A(創業者)", "②MVP/機能拡張", [
        ("意志",        "世界中の個人をスキルで繋ぐプラットフォームを作りたい", 1.0),
        ("求めている",  "プロトタイプを実装してくれるエンジニアが欲しい",        0.9),
        ("自分の能力",  "事業構想とユーザー理解は得意だがコードは書けない",      0.4),
    ])
    # --- 候補者たち ---
    cands=[
        Person("B(実装者)", "②MVP/機能拡張", [
            ("能力",   "多くのリポジトリでバックエンドを実装してきた実装力",   1.0),
            ("求めている","面白い構想に技術で参加したい",                       0.7),
        ]),
        Person("C(同種の構想家)", "②MVP/機能拡張", [
            ("意志",   "人と人をスキルで繋ぐサービスを構想している",            1.0),
            ("能力",   "事業構想とユーザーリサーチが得意",                      0.8),
        ]),
        Person("D(無関係)", "②MVP/機能拡張", [
            ("意志",   "料理レシピの動画を作るのが好き",                        1.0),
        ]),
    ]

    results, need_rationale = match(A, cands, table)

    print("### (3)系列予測層の出力: Aの必要像の根拠（遷移テーブルから）")
    for r in need_rationale:
        print(f"  - 特徴[{r['feature']}] を重視 (p_pos={r['p_positive_change']}, n={r['n']})")
    print(f"\n### (4)照合層の出力: {A.name} (phase={A.phase}) への接続候補ランキング")
    print(f"{'候補':<16}{'総合':<7}{'相補':<7}{'類似':<7} 要素対応(上位)")
    for r in results:
        pairs="; ".join(f"{a}~{b}:{s}" for a,b,s in r["top_element_pairs"])
        print(f"{r['candidate']:<16}{r['score']:<7}{r['complement']:<7}{r['similar']:<7} {pairs}")
    print("\n読み筋: B(実装者)は相補で勝ち、C(同種構想家)は類似で勝つ。")
    print("        Aが今ほしいのは相補(実装力)なので、相補重み0.6でBが上位に来れば結線成功。")

if __name__=="__main__":
    main()
