# ②接続層を ③の呼び出し方で実行するデモ。
# seeker = v3サンプル（依頼者本人の構造化情報）。candidate_pool = 確認用の仮プール。
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from connection_layer import run_matching

seeker = {
    "意志": "止まっているものを、正しさではなく『接続』で動かし直したい。起業家精神が『方法がない』だけで消えない世界を、汎用の構造化プロンプトと、それを蓄積するDBとして実装する。",
    "求めている": "目的・夢を共有できる多様な立場の人と、低コストで出会うこと。とりわけ最初の実証パートナー／最初に試してくれる相手。",
    "能力": "抽象概念を運用可能な構造へ落とす設計。思想・構造・MVP・実証を一枚に束ねる構成。",
    "フェーズ": "mvp",
}

# ③が共有DBから参照して渡す想定の候補プール（確認用の仮データ）。
# seekerの欠落=「最初に試す実証パートナー」を埋める相補相手を意図的に含める。
candidate_pool = [
    {"id": "P1_実証パートナー", "profile": "コミュニティ運営者。新しいツールを自分の場で最初に試し、フィードバックを返すのが好き。構造化や思考整理のツールを探していた。"},
    {"id": "P2_同志構想家",     "profile": "『人と人の出会いの非効率』を別アプローチ（イベント設計）で解こうとしている起業家。思想は近いが実装は持たない。"},
    {"id": "P3_実装者",         "profile": "複数プロダクトを実装してきたエンジニア。DB設計とAPIが得意。社会性のあるプロダクトに関わりたい。"},
    {"id": "P4_無関係",         "profile": "料理写真のインスタ運用と物販。"},
]

def my_judgment(seeker, cand, roles):
    # API認証なし環境のため、私(Claude)が候補を読んで二チャネルを0..1で採点（=LLM代行/仕様5.5節）。
    table = {
        # seekerの最大の欠落は「最初に試す実証パートナー」。意志は「接続で動かす構造化＋DB」。
        "P1_実証パートナー": {"comp": 0.85, "comp_via": "最初に試す実証パートナー", "sim": 0.55},
        "P2_同志構想家":     {"comp": 0.25, "comp_via": "最初に試す実証パートナー", "sim": 0.80},
        "P3_実装者":         {"comp": 0.70, "comp_via": "プロダクトを形にできる開発者", "sim": 0.35},
        "P4_無関係":         {"comp": 0.05, "comp_via": "最初に試す実証パートナー", "sim": 0.10},
    }
    return table[cand["id"]]

for want in ["complement", "similar"]:
    out = run_matching(seeker, candidate_pool, want=want, judge_fn=my_judgment)
    print(f"\n=== want='{want}'  必要像={[r['role'] for r in out['needed_roles']]} ===")
    print(f"{'順':<3}{'候補':<16}{'総合':<7}{'相補':<6}{'類似':<6}")
    for r in out["ranking"]:
        print(f"{r['rank']:<3}{r['id']:<16}{r['score']:<7}{r['comp']:<6}{r['sim']:<6}")
    top = out["ranking"][0]
    print(f"  1位の根拠: {top['reason']}")
