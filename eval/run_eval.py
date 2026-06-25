"""
PoX embedding モデル比較 評価ランナー（設計書 §7-4 / §2 E1/E3/E4）

使い方:
    MODEL_TAG=qwen3-emb-0.6b   POX_EMBED_BACKEND=qwen3   \\
    POX_QWEN3_ENDPOINT=<url>   python eval/run_eval.py

    MODEL_TAG=embgemma-300m    POX_EMBED_BACKEND=embgemma \\
    POX_EMBGEMMA_ENDPOINT=<url> python eval/run_eval.py

    MODEL_TAG=nomic-emb-v2     POX_EMBED_BACKEND=nomic   \\
    POX_NOMIC_ENDPOINT=<url>   python eval/run_eval.py

出力:
    Hit@1 / Hit@3 / MRR（全体 + 日本語のみ）を標準出力に表示。
    FULL_DIM と 256（MRL 後）の両方を測定（E4）。
    結果は eval/results/<MODEL_TAG>_<timestamp>.json にも保存する（TODO）。

注意（設計書 §4-1 / §8）:
    - 1回の計算で複数モデルのベクトルを混ぜない。MODEL_TAG を変えて3回別々に実行する。
    - 評価ケースに _comment 行（辞書に _comment キーを持つもの）が含まれる場合は読み飛ばす。
    - raw テキストは渡さない。embedding_service.build_vectors が redaction を行う。
"""
import sys
import os
import json
import pathlib
import datetime

# src を import パスに追加
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

EVAL_FILE = pathlib.Path(__file__).parent / "eval_pairs_v1.jsonl"
RESULTS_DIR = pathlib.Path(__file__).parent / "results"


# ── 評価セット読み込み ──────────────────────────────────────────────────────
def load_eval_pairs(path=EVAL_FILE):
    """JSONL を読み込む。_comment キーを持つ行は読み飛ばす。"""
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "_comment" in obj:
                continue
            cases.append(obj)
    return cases


# ── 指標計算 ────────────────────────────────────────────────────────────────
def hit_at_k(rank, k):
    """正解が rank 位（1始まり）に来ているかを返す。"""
    return 1 if rank <= k else 0


def mrr(rank):
    """平均逆順位の1ケース分（rank は1始まり）。"""
    return 1.0 / rank if rank >= 1 else 0.0


def compute_metrics(results):
    """
    results: [{"rank": int, "lang": "ja"|"en"}, ...]
    Hit@1, Hit@3, MRR（全体 + 日本語のみ）を返す。
    """
    # TODO: 実装する
    raise NotImplementedError("compute_metrics は未実装です（足場のみ）。")


# ── 1ケースの評価 ────────────────────────────────────────────────────────────
def eval_case(case, use_short_dim=False):
    """
    1 case を評価し、正解候補の順位を返す。

    TODO: 実装する。
    手順:
      1. seeker の will/now テキストを embedding_service でベクトル化（use_short_dim に応じて full or short を選択）
      2. 全 candidate をベクトル化
      3. 構成C的なスコアリング（簡易版: コサイン類似度）で順位付け
      4. label="match" の candidate の順位を返す
    注意: matcher_v4.py は触らない（設計書 §禁則）。
          ここでは embedding_service.embed を直接呼んでスコアリングする簡易実装でよい。
    """
    raise NotImplementedError("eval_case は未実装です（足場のみ）。")


# ── MRL 比較（FULL_DIM vs SHORT_DIM）────────────────────────────────────────
def eval_mrl(cases):
    """
    E4: FULL_DIM と SHORT_DIM=256 の両方で Hit@3 を測定し差を返す。

    TODO: eval_case(case, use_short_dim=False) と eval_case(case, use_short_dim=True)
          を両方呼んで比較する。
    """
    raise NotImplementedError("eval_mrl は未実装です（足場のみ）。")


# ── メイン ──────────────────────────────────────────────────────────────────
def main():
    from embedding_config import MODEL_TAG, BACKEND, FULL_DIM, SHORT_DIM

    print(f"=== PoX embedding 評価ランナー ===")
    print(f"MODEL_TAG : {MODEL_TAG}")
    print(f"BACKEND   : {BACKEND}")
    print(f"FULL_DIM  : {FULL_DIM}")
    print(f"SHORT_DIM : {SHORT_DIM}")
    print()

    cases = load_eval_pairs()
    if not cases:
        print("[警告] 評価ケースが0件です。eval/eval_pairs_v1.jsonl にケースを追加してください。")
        return

    print(f"評価ケース数: {len(cases)}")

    # TODO: 以下を実装する
    # results_full  = [eval_case(c, use_short_dim=False) for c in cases]
    # results_short = [eval_case(c, use_short_dim=True)  for c in cases]
    # metrics_full  = compute_metrics(results_full)
    # metrics_short = compute_metrics(results_short)
    # print_metrics(MODEL_TAG, metrics_full, metrics_short)
    # save_results(MODEL_TAG, metrics_full, metrics_short)

    print("[TODO] eval_case / compute_metrics が未実装のため、ここで終了します。")
    print("       GPU ホストを立てて backend を実装してから eval_case を完成させてください。")


if __name__ == "__main__":
    main()
