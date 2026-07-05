"""
3モデル実験結果の分析データ出力（人手評価の準備）

入力: eval/snapshots の最新 1,090 件×3モデル（TOP30・attribution・timing 付き）。
出力: eval/analysis/ に以下を生成（個人プロフィール本文はファイルにのみ出す）。
  1. comparison_report.md   モデル間の構造比較（Jaccard/Spearman/limiting_axis/スコア分布/固有候補）
  2. human_eval_sheet.md    モデル別 TOP30 評価シート（ネタばらし版）
  3. human_eval_sheet.csv   同内容 CSV（UTF-8 BOM）
  4. timing_report.md       効率化の検証（段階別 timing・キャッシュ効果）
  5. blind_eval_sheet.md + blind_key.json  ブラインド評価シート（最初に使う）

標準出力には統計値・件数・パスのみ出す（候補本文は出さない）。
既存コード・スナップショットは変更しない。solo_eval / サーバー / Claude は実行しない。
"""
import json, glob, os, math, random, csv, statistics, pathlib
from collections import Counter

BASE       = pathlib.Path(__file__).parent.parent
SNAP_DIR   = BASE / "snapshots"
OUT_DIR    = BASE / "analysis"
CACHE_DIR  = BASE / "cache"
BLIND_SEED = 20260705  # ブラインドシャッフルの seed（再現可能）

MODELS = [  # 表示名, model_tag（表示順もこの通り）
    ("Qwen3",    "qwen3-embedding-0.6b-d1024"),
    ("EmbGemma", "embgemma-300m"),
    ("Nomic",    "nomic-emb-v2"),
]

# 1回目（キャッシュ構築）の候補ベクトル化秒。実行ログ由来（該当スナップショットは未コミットのため
# ここに記録値を保持。2回目＝キャッシュ後の値は各スナップショットの timing から読む）。
FIRST_RUN_CAND_SEC = {
    "Qwen3":    20266.05,  # ※PCスリープ時間を含む異常値
    "EmbGemma":  2383.51,
    "Nomic":     2025.62,
}

CHANNELS_4 = ("will_symmetric", "will_passage", "state_passage", "necessity_query")


# ── ユーティリティ ──────────────────────────────────────────────────────────
def _clean(text, n=150):
    """マークダウン表・CSV 用に改行と区切り文字をならし、n 字に切る。"""
    if not isinstance(text, str):
        text = ""
    t = " ".join(text.split())          # 改行・連続空白をスペース1個に
    t = t.replace("|", "／")            # 表の区切りを壊さない
    if len(t) > n:
        t = t[:n] + "…"
    return t


def jaccard(a, b):
    a, b = set(a), set(b)
    u = a | b
    return (len(a & b) / len(u)) if u else 0.0


def _rankdata(vals):
    """同順位は平均順位（Spearman 用）。"""
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
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def spearman_on_common(ranks_a, ranks_b):
    """
    2モデルに共通して登場する候補の、各モデル rank から Spearman ρ。
    ranks_a/ranks_b は {login: rank} 。共通 login 部分のみで計算。
    """
    common = sorted(set(ranks_a) & set(ranks_b))
    if len(common) < 3:
        return None, len(common)
    ra = _rankdata([ranks_a[l] for l in common])
    rb = _rankdata([ranks_b[l] for l in common])
    return _pearson(ra, rb), len(common)


# ── スナップショット読み込み ────────────────────────────────────────────────
def load_snapshots():
    out = {}
    for disp, tag in MODELS:
        chosen = None
        for f in sorted(glob.glob(str(SNAP_DIR / f"solo_KH_{tag}_*.json"))):
            d = json.load(open(f, encoding="utf-8"))
            if d.get("pool_size") == 1090:
                chosen = (f, d)   # 最新（ソート後の最後）を採用
        if not chosen:
            raise SystemExit(f"[停止] {disp}: pool_size=1090 のスナップショットが見つかりません。")
        f, d = chosen
        top = d.get("top", [])
        if len(top) != 30:
            raise SystemExit(f"[停止] {disp}: top 件数が 30 ではありません（{len(top)}）。")
        if not all(t.get("attribution") for t in top):
            raise SystemExit(f"[停止] {disp}: attribution が欠けている候補があります。")
        if not isinstance(d.get("timing"), dict):
            raise SystemExit(f"[停止] {disp}: timing キーがありません。")
        out[disp] = {"path": f, "data": d, "top": top}
    return out


# ── 各成果物 ────────────────────────────────────────────────────────────────
def build_comparison(snaps):
    logins = {m: [t["login"] for t in s["top"]] for m, s in snaps.items()}
    rank = {m: {t["login"]: t["rank"] for t in s["top"]} for m, s in snaps.items()}
    names = [m for m, _ in MODELS]

    def topN(m, n): return logins[m][:n]

    # Jaccard/共通数（TOP10, TOP30）
    pair_stats = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            row = {}
            for n in (10, 30):
                inter = set(topN(a, n)) & set(topN(b, n))
                row[n] = {"common": len(inter), "jaccard": jaccard(topN(a, n), topN(b, n))}
            rho, ncomm = spearman_on_common(rank[a], rank[b])
            row["spearman"] = rho
            row["spearman_n"] = ncomm
            pair_stats[(a, b)] = row

    common10 = set(topN(names[0], 10))
    common30 = set(topN(names[0], 30))
    for m in names[1:]:
        common10 &= set(topN(m, 10))
        common30 &= set(topN(m, 30))

    la = {m: Counter(t["attribution"]["limiting_axis"] for t in snaps[m]["top"]) for m in names}
    scores = {m: [t["score"] for t in snaps[m]["top"]] for m in names}

    # 固有候補（そのモデル TOP30 にだけ）
    uniq = {}
    for m in names:
        others = set()
        for o in names:
            if o != m:
                others |= set(logins[o])
        uniq[m] = [l for l in logins[m] if l not in others]

    L = []
    L.append("# 3モデル構造比較レポート（1,090件プール・TOP30）\n")
    L.append("入力スナップショット:")
    for m in names:
        L.append(f"- {m}: `{os.path.basename(snaps[m]['path'])}`")
    L.append("\n---\n")

    L.append("## 1. 上位の重なり（共通候補数 / Jaccard）\n")
    L.append("| ペア | TOP10 共通 | TOP10 Jaccard | TOP30 共通 | TOP30 Jaccard |")
    L.append("|---|---|---|---|---|")
    for (a, b), r in pair_stats.items():
        L.append(f"| {a}∩{b} | {r[10]['common']} | {r[10]['jaccard']:.3f} "
                 f"| {r[30]['common']} | {r[30]['jaccard']:.3f} |")
    L.append(f"\n- **3モデル共通（TOP10）= {len(common10)} 件**: {', '.join('@'+x for x in sorted(common10)) or '（なし）'}")
    L.append(f"- **3モデル共通（TOP30）= {len(common30)} 件**: {', '.join('@'+x for x in sorted(common30)) or '（なし）'}\n")

    L.append("## 2. 順位相関（Spearman ρ・共通候補で計算）\n")
    L.append("| ペア | 共通候補数 | Spearman ρ |")
    L.append("|---|---|---|")
    for (a, b), r in pair_stats.items():
        rho = f"{r['spearman']:.3f}" if r["spearman"] is not None else "n<3"
        L.append(f"| {a}∩{b} | {r['spearman_n']} | {rho} |")
    L.append("\n（ρが高いペアほど順位構造が似る。90件時は Qwen3≒Nomic が近く EmbGemma が別だった。）\n")

    L.append("## 3. limiting_axis 分布（TOP30）\n")
    L.append("| モデル | a | b | c |")
    L.append("|---|---|---|---|")
    for m in names:
        L.append(f"| {m} | {la[m].get('a',0)} | {la[m].get('b',0)} | {la[m].get('c',0)} |")
    L.append("")

    L.append("## 4. スコア分布（TOP30・分布の形のみ。絶対値のモデル間比較はしない）\n")
    L.append("| モデル | min | max | 幅 | mean | std |")
    L.append("|---|---|---|---|---|---|")
    for m in names:
        s = scores[m]
        L.append(f"| {m} | {min(s):.4f} | {max(s):.4f} | {max(s)-min(s):.4f} "
                 f"| {statistics.mean(s):.4f} | {statistics.pstdev(s):.4f} |")
    L.append("")

    L.append("## 5. 各モデルの固有候補（そのモデル TOP30 のみに登場）\n")
    for m in names:
        L.append(f"- **{m}**（{len(uniq[m])}件）: {', '.join('@'+x for x in uniq[m]) or '（なし）'}")
    L.append("")

    (OUT_DIR / "comparison_report.md").write_text("\n".join(L), encoding="utf-8")
    return {"pair_stats": pair_stats, "common10": common10, "common30": common30,
            "la": la, "scores": scores, "uniq": uniq, "rank": rank, "logins": logins}


def build_human_sheet(snaps):
    names = [m for m, _ in MODELS]
    header_cols = ["rank", "login", "name", "score", "limiting_axis",
                   "意志(will)要約", "現状(state)要約", "評価(空)", "メモ(空)"]

    # md
    L = ["# KH 人手評価シート（モデル別・ネタばらし版）\n",
         "※ **まず `blind_eval_sheet.md` でブラインド評価を終えてから**開くこと。\n",
         "## 評価の観点（引き継ぎ書 §4-2②）",
         "1. 会いたい順との一致度",
         "2. 上位の納得感（なぜ噛み合うか腑に落ちるか）",
         "3. 取りこぼし感（来るべき人が来ていない感覚）",
         "4. 数件でよいので「なぜ納得か」の言語化\n", "---\n"]
    for m in names:
        L.append(f"## {m}\n")
        L.append("| " + " | ".join(header_cols) + " |")
        L.append("|" + "|".join("---" for _ in header_cols) + "|")
        for t in snaps[m]["top"]:
            la = t["attribution"]["limiting_axis"]
            L.append("| " + " | ".join([
                str(t["rank"]), "@" + t["login"], _clean(t.get("name", ""), 40),
                f"{t['score']:.4f}", la,
                _clean(t.get("will", "")), _clean(t.get("state", "")), "", "",
            ]) + " |")
        L.append("")
    # 共通・固有の再掲
    logins = {m: [t["login"] for t in snaps[m]["top"]] for m in names}
    common30 = set(logins[names[0]])
    for m in names[1:]:
        common30 &= set(logins[m])
    L.append("## 参考：3モデル共通候補（TOP30）\n")
    L.append(", ".join("@" + x for x in sorted(common30)) or "（なし）")
    L.append("\n## 参考：各モデル固有候補（TOP30）\n")
    for m in names:
        others = set()
        for o in names:
            if o != m:
                others |= set(logins[o])
        uq = [l for l in logins[m] if l not in others]
        L.append(f"- **{m}**: " + (", ".join("@" + x for x in uq) or "（なし）"))
    (OUT_DIR / "human_eval_sheet.md").write_text("\n".join(L), encoding="utf-8")

    # csv（UTF-8 BOM）
    with open(OUT_DIR / "human_eval_sheet.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["model"] + header_cols)
        for m in names:
            for t in snaps[m]["top"]:
                w.writerow([m, t["rank"], t["login"], t.get("name", ""),
                            f"{t['score']:.4f}", t["attribution"]["limiting_axis"],
                            _clean(t.get("will", ""), 300), _clean(t.get("state", ""), 300), "", ""])


def build_timing(snaps):
    names = [m for m, _ in MODELS]
    L = ["# 効率化の検証（ベクトルキャッシュ）\n",
         "段階別 timing（秒）。(a)必要像解決 (b)seekerベクトル化 (c)候補ベクトル化 (d)スコアリング。\n",
         "各スナップショットは**キャッシュ後の実行**（2回目相当）の timing を保持。",
         "1回目（キャッシュ構築）の (c) は実行ログ由来の記録値。\n",
         "| モデル | (c) 1回目 | (c) 2回目(snapshot) | hit/miss(2回目) | (a) | (b) | (d) |",
         "|---|---|---|---|---|---|---|"]
    two_c = {}
    for m in names:
        tm = snaps[m]["data"]["timing"]
        c2 = tm.get("cand_vec_sec", float("nan"))
        two_c[m] = c2
        L.append(f"| {m} | {FIRST_RUN_CAND_SEC.get(m,'-'):.2f} | {c2:.2f} "
                 f"| {tm.get('cache_hits','-')}/{tm.get('cache_misses','-')} "
                 f"| {tm.get('necessity_sec',0):.2f} | {tm.get('seeker_vec_sec',0):.2f} | {tm.get('scoring_sec',0):.2f} |")
    L.append("")
    # キャッシュファイル
    L.append("## キャッシュファイル\n")
    npz = sorted(glob.glob(str(CACHE_DIR / "*.npz")))
    if npz:
        for p in npz:
            L.append(f"- `{p}` : {os.path.getsize(p):,} bytes")
    else:
        L.append("- （この環境に `eval/cache/*.npz` は存在しない＝gitignore・実行PCローカルのみ）")
    L.append("")
    # 一行結論
    worst_first = max(FIRST_RUN_CAND_SEC.values())
    best_second = min(v for v in two_c.values() if v == v)
    L.append("## 結論\n")
    L.append(f"候補ベクトル化: 約 **{worst_first/60:.0f}〜{min(FIRST_RUN_CAND_SEC.values())/60:.0f}分（1回目）→ "
             f"最速 {best_second:.1f}秒（2回目・キャッシュ後）**。キャッシュ効果を確認。")
    (OUT_DIR / "timing_report.md").write_text("\n".join(L), encoding="utf-8")
    return two_c


def build_blind(snaps):
    names = [m for m, _ in MODELS]
    # 和集合（login で重複排除）。プロフィール本文は最初に出たモデルから取る。
    union = {}   # login -> {name, will, state}
    rankmap = {} # login -> {model: rank}
    for m in names:
        for t in snaps[m]["top"]:
            lg = t["login"]
            if lg not in union:
                union[lg] = {"name": t.get("name", ""), "will": t.get("will", ""),
                             "state": t.get("state", "")}
            rankmap.setdefault(lg, {})[m] = t["rank"]

    logins = list(union.keys())
    rng = random.Random(BLIND_SEED)
    rng.shuffle(logins)

    # 匿名ID 付与
    key = {"seed": BLIND_SEED, "map": {}}
    rows = []
    for i, lg in enumerate(logins, 1):
        bid = f"B-{i:03d}"
        key["map"][bid] = {
            "login": lg, "name": union[lg]["name"],
            "qwen3":    rankmap[lg].get("Qwen3"),
            "embgemma": rankmap[lg].get("EmbGemma"),
            "nomic":    rankmap[lg].get("Nomic"),
        }
        rows.append((bid, lg))

    # blind_eval_sheet.md（login・rank・score・model・la は伏せる。name は残す）
    L = ["# ブラインド評価シート（最初に使う）\n",
         "## 手順",
         "1. **この表だけ**を見て、モデルを想像せずに『会いたい度』(1〜5) を付ける",
         "2. 付け終わるまで `blind_key.json` と `human_eval_sheet.md` を開かない",
         "3. 終わったら `python eval/scripts/score_blind_eval.py` で突合\n",
         f"（総行数 {len(rows)} ／ seed {BLIND_SEED}）\n", "---\n",
         "| 匿名ID | name | 意志(will)要約 | 現状(state)要約 | 会いたい度(1〜5) | メモ |",
         "|---|---|---|---|---|---|"]
    for bid, lg in rows:
        u = union[lg]
        L.append(f"| {bid} | {_clean(u['name'],40)} | {_clean(u['will'])} | {_clean(u['state'])} |  |  |")
    (OUT_DIR / "blind_eval_sheet.md").write_text("\n".join(L), encoding="utf-8")

    # blind_eval_sheet.csv（記入用・BOM）
    with open(OUT_DIR / "blind_eval_sheet.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["匿名ID", "name", "意志要約", "現状要約", "会いたい度", "メモ"])
        for bid, lg in rows:
            u = union[lg]
            w.writerow([bid, u["name"], _clean(u["will"], 300), _clean(u["state"], 300), "", ""])

    json.dump(key, open(OUT_DIR / "blind_key.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return len(rows)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    snaps = load_snapshots()
    comp = build_comparison(snaps)
    build_human_sheet(snaps)
    two_c = build_timing(snaps)
    n_union = build_blind(snaps)

    # ── 標準出力（統計値・件数・パスのみ。候補本文は出さない）──
    names = [m for m, _ in MODELS]
    print("生成物:", OUT_DIR)
    for fn in ["comparison_report.md", "human_eval_sheet.md", "human_eval_sheet.csv",
               "timing_report.md", "blind_eval_sheet.md", "blind_eval_sheet.csv", "blind_key.json"]:
        print("  -", (OUT_DIR / fn))
    print("\n[Jaccard/共通] (TOP10, TOP30)")
    for (a, b), r in comp["pair_stats"].items():
        print(f"  {a}∩{b}: TOP10 共通{r[10]['common']} J={r[10]['jaccard']:.3f} / "
              f"TOP30 共通{r[30]['common']} J={r[30]['jaccard']:.3f}")
    print("[Spearman] 共通候補で")
    for (a, b), r in comp["pair_stats"].items():
        rho = f"{r['spearman']:.3f}" if r["spearman"] is not None else "n<3"
        print(f"  {a}∩{b}: n={r['spearman_n']} rho={rho}")
    print("[limiting_axis TOP30]")
    for m in names:
        c = comp["la"][m]
        print(f"  {m}: a={c.get('a',0)} b={c.get('b',0)} c={c.get('c',0)}")
    print("[score TOP30 min..max 幅]")
    for m in names:
        s = comp["scores"][m]
        print(f"  {m}: {min(s):.4f}..{max(s):.4f} 幅{max(s)-min(s):.4f}")
    print(f"[3モデル共通] TOP10={len(comp['common10'])} TOP30={len(comp['common30'])}")
    print(f"[ブラインド和集合] {n_union} 行")
    print("[固有候補件数]", {m: len(comp["uniq"][m]) for m in names})


if __name__ == "__main__":
    main()
