"""
ブラインド評価の突合スクリプト（KH が blind_eval_sheet を記入後に実行）

入力:
  - eval/analysis/blind_eval_sheet.csv  … KH が「会いたい度」列を記入したもの（推奨）
    （md しか無い場合は --md で eval/analysis/blind_eval_sheet.md をパース）
  - eval/analysis/blind_key.json        … 匿名ID→login・各モデル rank の対応表

記入フォーマットの想定:
  - 「会いたい度」は 1〜5（整数）。空欄は「未評価」として集計から除外。
  - 全角数字（１〜５）・前後空白・"3点" のような表記ゆれは吸収する。
  - 1〜5 の範囲外や数値化できない値は無視（警告表示）。

出力（標準出力）:
  - モデルごとの会いたい度 平均・件数・分布（そのモデル TOP30 に入っていた候補群で集計）
  - モデル rank と会いたい度の Spearman 相関（会いたい順との一致度の定量版）
  - 取りこぼし分析: 会いたい度が高い(既定>=4)のに、特定モデルだけ TOP30 に入れていない候補
使い方:
  python eval/scripts/score_blind_eval.py            # csv を読む
  python eval/scripts/score_blind_eval.py --md       # md を読む
  python eval/scripts/score_blind_eval.py --hi 5     # 取りこぼし閾値を変更（既定4）
"""
import json, csv, sys, re, math, pathlib
from collections import defaultdict

BASE = pathlib.Path(__file__).parent.parent
ANALYSIS = BASE / "analysis"
KEY_FILE = ANALYSIS / "blind_key.json"
CSV_FILE = ANALYSIS / "blind_eval_sheet.csv"
MD_FILE  = ANALYSIS / "blind_eval_sheet.md"
MODELS = ["qwen3", "embgemma", "nomic"]

_Z2H = str.maketrans("０１２３４５６７８９", "0123456789")


def parse_score(raw):
    """会いたい度セルを 1〜5 の int に。空/不正は None。"""
    if raw is None:
        return None
    s = str(raw).translate(_Z2H).strip()
    if not s:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    v = float(m.group())
    if v != v or v < 1 or v > 5:
        return None
    return int(round(v))


def load_scores_csv():
    out = {}
    with open(CSV_FILE, encoding="utf-8-sig", newline="") as fh:
        r = csv.reader(fh)
        header = next(r, None)
        # 「匿名ID」列と「会いたい度」列の位置を特定
        idx_id = 0
        idx_sc = None
        for i, h in enumerate(header or []):
            if "会いたい" in str(h):
                idx_sc = i
            if "匿名" in str(h) or str(h).strip().upper().startswith("B"):
                idx_id = i
        if idx_sc is None:
            idx_sc = 4  # 既定レイアウト
        for row in r:
            if not row or len(row) <= idx_sc:
                continue
            bid = row[idx_id].strip()
            if not bid:
                continue
            out[bid] = parse_score(row[idx_sc])
    return out


def load_scores_md():
    out = {}
    for line in MD_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("| B-"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # 列: 匿名ID | name | will | state | 会いたい度 | メモ
        if len(cells) >= 5:
            out[cells[0]] = parse_score(cells[4])
    return out


def _rankdata(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(x, y):
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    dx = math.sqrt(sum((a - mx) ** 2 for a in x))
    dy = math.sqrt(sum((b - my) ** 2 for b in y))
    if dx == 0 or dy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (dx * dy)


def spearman(rank_vals, score_vals):
    if len(rank_vals) < 3:
        return None
    return _pearson(_rankdata(rank_vals), _rankdata(score_vals))


def main():
    use_md = "--md" in sys.argv
    hi = 4
    if "--hi" in sys.argv:
        try:
            hi = int(sys.argv[sys.argv.index("--hi") + 1])
        except Exception:
            pass

    if not KEY_FILE.exists():
        raise SystemExit(f"[停止] {KEY_FILE} が無い。先に analyze_3models.py を実行。")
    key = json.load(open(KEY_FILE, encoding="utf-8"))["map"]
    scores = load_scores_md() if use_md else load_scores_csv()

    filled = {k: v for k, v in scores.items() if v is not None}
    print(f"記入済み: {len(filled)} / {len(key)} 行\n")
    if not filled:
        raise SystemExit("会いたい度が1つも記入されていません。blind_eval_sheet を記入してから再実行。")

    # モデルごと集計
    print("=== モデル別：会いたい度（そのモデル TOP30 に入っていた候補群）===")
    per_model_pairs = {m: [] for m in MODELS}  # (rank, score)
    for bid, info in key.items():
        sc = scores.get(bid)
        if sc is None:
            continue
        for m in MODELS:
            if info.get(m):  # rank が None でない＝そのモデル TOP30 に居た
                per_model_pairs[m].append((info[m], sc))
    for m in MODELS:
        pairs = per_model_pairs[m]
        if not pairs:
            print(f"  {m}: 該当なし")
            continue
        vals = [s for _, s in pairs]
        dist = {k: vals.count(k) for k in range(1, 6)}
        avg = sum(vals) / len(vals)
        rho = spearman([r for r, _ in pairs], [s for _, s in pairs])
        rho_s = f"{rho:.3f}" if rho is not None else "n<3"
        print(f"  {m}: n={len(vals)} 平均={avg:.2f} 分布={dist} rank-会いたい度 Spearman={rho_s}")
    print()

    # 取りこぼし: 会いたい度>=hi なのに、特定モデルだけ入れていない
    print(f"=== 取りこぼし分析（会いたい度>={hi} の候補を、どのモデルが TOP30 に入れ損ねたか）===")
    any_row = False
    for bid, info in key.items():
        sc = scores.get(bid)
        if sc is None or sc < hi:
            continue
        missed = [m for m in MODELS if not info.get(m)]
        got = [m for m in MODELS if info.get(m)]
        if missed:
            any_row = True
            print(f"  {bid} @{info['login']}（会いたい度{sc}）: 取得={got or '（なし）'} / 取りこぼし={missed}")
    if not any_row:
        print("  高評価候補はすべてのモデルが拾えていた（または該当なし）。")


if __name__ == "__main__":
    main()
