"""
1人の seeker（＝あなた自身）を固定して、GitHub プロフィールプール 90 件から
マッチング上位を出すデモスクリプト。

eval_pairs（評価ケース集）の代わりに、eval/my_profile.json に書いた
「あなたの意志・現状」を seeker として使う。

使い方（PowerShell。Qwen3 サーバー起動済み前提）:
    # 1. eval/my_profile.json を自分の内容に書き換える（下のテンプレを参照）
    # 2. 環境変数をセット
    $env:POX_EMBED_MODEL_TAG = "qwen3-embedding-0.6b-d1024"
    $env:POX_EMBED_BACKEND   = "qwen3"
    $env:POX_QWEN3_ENDPOINT  = "http://localhost:8000/embed"
    $env:ANTHROPIC_API_KEY   = "sk-ant-..."
    # 3. 実行
    python eval/scripts/solo_eval.py

    # 上位件数を変えたいとき（既定 10）:
    $env:SOLO_EVAL_TOP_N = "20"

my_profile.json の形式（eval_pairs の seeker と同じ will/now 形式）:
    {
      "name": "あなたの表示名（任意）",
      "will": "あなたが本当に実現したいこと（意志）",
      "now":  "いまの現状・持っているもの・置かれている状況",
      "state_can_type": "（任意）動き方の型。例: つなぐ人 / 作る人 / 発信する人",
      "state_bound":    "（任意）所在地・所属・時間などの縛り",
      "state_unsorted": "（任意）その他の文脈"
    }
"""
import sys, os, json, pathlib, datetime, traceback, time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).parent))  # vector_cache（同ディレクトリ）

from necessity_gen import generate_necessity
from embedding_service import build_vectors
from matcher_v4 import rank_candidates
from embedding_config import MODEL_TAG
import vector_cache
import distribution_log


def _s(v):
    """str 以外（dict・list・None 等）を空文字に落とす。"""
    return v if isinstance(v, str) else ""


PROFILE_FILE = pathlib.Path(__file__).parent.parent / "my_profile.json"
POOL_FILE    = pathlib.Path(__file__).parent.parent / "github_profiles.jsonl"
RESULTS_DIR  = pathlib.Path(__file__).parent.parent / "results"
TOP_N        = int(os.environ.get("SOLO_EVAL_TOP_N", "10"))
# 何件ビルドするごとにキャッシュをチェックポイント保存するか（クラッシュ耐性）。
FLUSH_EVERY  = int(os.environ.get("POX_CACHE_FLUSH_EVERY", "25"))


def load_my_profile():
    """
    my_profile.json を読み込み、フラット形式の seeker_profile dict を返す。
    v4 形式（seeker.意志 / seeker.現状.*）とフラット形式（will / now）の両方に対応。
    supporting_material があればそのまま渡す（②生成の精度が上がる）。
    """
    if not PROFILE_FILE.exists():
        print(f"ERROR: {PROFILE_FILE} が見つかりません。", file=sys.stderr)
        print("eval/my_profile.json を作成し、あなたの意志・現状を記入してください。", file=sys.stderr)
        sys.exit(1)
    with open(PROFILE_FILE, encoding="utf-8") as f:
        raw = json.load(f)

    # v4 形式（schema_version=v4 または seeker.意志 が存在）
    if raw.get("schema_version") == "v4" or isinstance(raw.get("seeker"), dict):
        sk = raw.get("seeker", {})
        gj = sk.get("現状", {}) if isinstance(sk.get("現状"), dict) else {}
        prof = {
            "name":           raw.get("id") or raw.get("name") or "seeker",
            "will_text":      _s(sk.get("意志")),
            "state_have":     _s(gj.get("持っているもの")),
            "state_can_type": _s(gj.get("できること_型")),
            "state_bound":    _s(gj.get("縛られているもの")),
            "state_unsorted": _s(gj.get("未分類")),
            "supporting_material": raw.get("supporting_material", {}),
        }
    else:
        # フラット形式（旧テンプレ: will / now キー）
        prof = {
            "name":           raw.get("name") or "seeker",
            "will_text":      _s(raw.get("will")),
            "state_have":     _s(raw.get("now")),
            "state_can_type": _s(raw.get("state_can_type")),
            "state_bound":    _s(raw.get("state_bound")),
            "state_unsorted": _s(raw.get("state_unsorted")),
            "supporting_material": raw.get("supporting_material", {}),
        }

    # 必要像が事前に紐づいていれば持ち回す（②生成スキップ用）
    if isinstance(raw.get("derived_necessity"), dict):
        prof["derived_necessity"] = raw["derived_necessity"]

    if not prof["will_text"] and not prof["state_have"]:
        print("ERROR: プロフィールの意志・現状が両方空です。記入してください。", file=sys.stderr)
        sys.exit(1)
    return prof


def load_pool():
    if not POOL_FILE.exists():
        print(f"ERROR: {POOL_FILE} が見つかりません。", file=sys.stderr)
        print("先に eval/scripts/fetch_github_profiles.py を実行してください。", file=sys.stderr)
        sys.exit(1)
    pool = []
    with open(POOL_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pool.append(json.loads(line))
    return pool


def _to_vecs(profile):
    """build_vectors の結果を rank_candidates 用の4チャネル dict に変換。"""
    v = build_vectors(profile, "")
    return {
        "will_symmetric":  v["will_symmetric"],
        "will_passage":    v["will_passage"],
        "state_passage":   v["state_passage"],
        "necessity_query": v["necessity_query"],
    }


def _resolve_necessity(profile):
    """
    必要像を解決する。プロフィールに derived_necessity が紐づいていればそれを使い
    （②生成を再実行しない＝ANTHROPIC_API_KEY 不要）、無ければ generate_necessity を呼ぶ。
    """
    bound = profile.get("derived_necessity")
    if isinstance(bound, dict) and bound.get("necessity_text"):
        print("  （プロフィールに紐づく必要像を使用：Claude 呼び出しをスキップ）", flush=True)
        return {
            "necessity_text": bound["necessity_text"],
            "gamma":          float(bound.get("gamma", 0.0)),
            "p_sharpness":    float(bound.get("p_sharpness", 0.0)),
            "alpha":          float(bound.get("alpha", 1.0)),
            "beta":           float(bound.get("beta", 1.0)),
        }
    print("  （必要像を Claude で新規生成：ANTHROPIC_API_KEY が必要）", flush=True)
    return generate_necessity(profile)


def _seeker_vecs_and_params(profile):
    """seeker: 必要像解決 → build_vectors → 4チャネル dict + gamma/p/alpha/beta。"""
    nec  = _resolve_necessity(profile)
    v    = build_vectors(profile, nec["necessity_text"])
    vecs = {
        "will_symmetric":  v["will_symmetric"],
        "will_passage":    v["will_passage"],
        "state_passage":   v["state_passage"],
        "necessity_query": v["necessity_query"],
    }
    return vecs, nec["necessity_text"], nec["gamma"], nec["p_sharpness"], nec["alpha"], nec["beta"]


def main():
    print(f"モデル: {MODEL_TAG}")
    me   = load_my_profile()
    pool = load_pool()
    me_name = me.get("name") or "seeker"
    print(f"Seeker: {me_name}（固定）/ プール: {len(pool)} 件\n")

    # load_my_profile() が v4→フラット変換済みの dict を返すのでそのまま使う
    seeker_profile = me

    timing = {}  # 段階別計時（秒）＋キャッシュヒット率。snapshot に追加保存。

    # ── (a) seeker の必要像を解決 ────────────────────────────────────────
    print("あなたの必要像を解決...", flush=True)
    _t = time.perf_counter()
    try:
        nec = _resolve_necessity(seeker_profile)
    except Exception as e:
        traceback.print_exc()
        print(f"ERROR (necessity): {e}")
        return
    timing["necessity_sec"] = time.perf_counter() - _t
    necessity_text = nec["necessity_text"]
    gamma = nec["gamma"]; p = nec["p_sharpness"]; alpha = nec["alpha"]; beta = nec["beta"]

    # ── (b) seeker のベクトル化 ──────────────────────────────────────────
    _t = time.perf_counter()
    try:
        _sv = build_vectors(seeker_profile, necessity_text)
    except Exception as e:
        traceback.print_exc()
        print(f"ERROR (seeker vecs): {e}")
        return
    svecs = {k: _sv[k] for k in ("will_symmetric", "will_passage", "state_passage", "necessity_query")}
    timing["seeker_vec_sec"] = time.perf_counter() - _t
    print(f"\n【あなたの必要像】\n  {necessity_text}\n")
    print(f"  パラメータ: gamma={gamma:.3f} p={p:.3f} alpha={alpha:.3f} beta={beta:.3f}\n")

    # 蓄積の単位＝必要像。seeker ベクトル（full+256）を保存（MRL256 検証用・サーバー不要化）。
    nid = distribution_log.necessity_id(necessity_text)
    distribution_log.save_seeker_vecs(MODEL_TAG, nid, _sv)
    print(f"  necessity_id={nid} ｜ この必要像での蓄積: "
          f"{distribution_log.count_prior(MODEL_TAG, nid) + 1} 回目\n")

    # ── (c) 候補ベクトル化（モデル別キャッシュ経由）────────────────────────
    #   前回と入力テキストが同じ候補はキャッシュから読み出し（build_vectors を呼ばない）。
    cache = vector_cache.load_cache(MODEL_TAG)
    print(f"候補プールのベクトル化を開始（{len(pool)} 件・キャッシュ {len(cache)} 件）...", flush=True)
    _t = time.perf_counter()
    hits = misses = 0
    built_since_flush = 0
    cand_list = []        # [(login, cvecs), ...]
    rec_by_login = {}
    for i, p_rec in enumerate(pool, 1):
        login = p_rec.get("github_login", "?")
        cand_profile = {
            "will_text":      _s(p_rec.get("will_text")),
            "state_have":     _s(p_rec.get("state_have")),
            "state_can_type": _s(p_rec.get("state_can_type")),
            "state_bound":    _s(p_rec.get("state_bound")),
            "state_unsorted": _s(p_rec.get("state_unsorted")),
        }
        try:
            cached = vector_cache.is_cached(login, cand_profile, MODEL_TAG, cache)
            if cached:
                hits += 1
            else:
                misses += 1
            full = vector_cache.get_or_build(login, cand_profile, MODEL_TAG, cache)
            cvecs = {k: full[k] for k in ("will_symmetric", "will_passage", "state_passage", "necessity_query")}
            cand_list.append((login, cvecs))
            rec_by_login[login] = p_rec
            # 新規ビルドが FLUSH_EVERY 件たまるごとにチェックポイント保存（クラッシュ耐性）。
            if not cached:
                built_since_flush += 1
                if built_since_flush >= FLUSH_EVERY:
                    vector_cache.save_cache(MODEL_TAG, cache)
                    built_since_flush = 0
        except Exception as e:
            print(f"\n  skip {login}: {e}")
        if i % 5 == 0 or i == len(pool):
            print(f"\r  候補ベクトル化: {i}/{len(pool)} 完了（hit {hits} / miss {misses}）", end="", flush=True)
    print()
    vector_cache.save_cache(MODEL_TAG, cache)  # 最終保存（残り分を永続化）
    timing["cand_vec_sec"] = time.perf_counter() - _t
    timing["cache_hits"] = hits
    timing["cache_misses"] = misses

    if not cand_list:
        print("候補ベクトルが1件も作れませんでした。終了します。")
        return
    print(f"候補ベクトル化 完了: {len(cand_list)} 件（hit {hits} / miss {misses}）\n")

    # ── (d) ランキング ──────────────────────────────────────────────────
    #   全候補を採点（top_k=None）→ 分布蓄積に全件、表示/snapshot には先頭 TOP_N を使う。
    _t = time.perf_counter()
    ranked_all = rank_candidates(
        svecs, cand_list,
        gamma=gamma, p=p, alpha=alpha, beta=beta,
        top_k=None,
    )
    timing["scoring_sec"] = time.perf_counter() - _t

    # 全件スコア分布を蓄積（判定はしない・蓄積のみ）。
    run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dist_jsonl, dist_summary = distribution_log.save_distribution(MODEL_TAG, nid, ranked_all, run_ts)
    print(f"全件スコア分布を保存: {dist_jsonl.name} / {dist_summary.name}\n")

    ranked = ranked_all[:TOP_N]

    print(f"=== {me_name} に対するマッチング上位 {len(ranked)} 件 ===\n")
    top = []
    for rank, r in enumerate(ranked, 1):
        login = r["candidate_id"]
        score = r["score"]
        rec   = rec_by_login.get(login, {})
        print(f"  Rank {rank}: {rec.get('display_name', login)} (@{login})  score={score:.4f}")
        print(f"           意志: {rec.get('will_text','')[:70]}")
        print(f"           現状: {rec.get('state_have','')[:70]}")
        top.append({
            "rank":  rank,
            "login": login,
            "name":  rec.get("display_name", login),
            "score": round(score, 6),
            "will":  rec.get("will_text", ""),
            "state": rec.get("state_have", ""),
            "attribution": r.get("attribution"),
        })
    print()

    # ── 結果保存 ────────────────────────────────────────────────────────
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "model_tag":      MODEL_TAG,
        "seeker_name":    me_name,
        "seeker_will":    seeker_profile["will_text"],
        "seeker_now":     seeker_profile["state_have"],
        "necessity_text": necessity_text,
        "params": {"gamma": gamma, "p": p, "alpha": alpha, "beta": beta},
        "pool_size":      len(pool),
        "timing":         timing,
        "top":            top,
    }

    # ── 段階別計時サマリー（標準出力）────────────────────────────────────
    print("段階別計時（秒）:")
    print(f"  (a) 必要像解決    : {timing.get('necessity_sec', 0):.2f}")
    print(f"  (b) seekerベクトル: {timing.get('seeker_vec_sec', 0):.2f}")
    print(f"  (c) 候補ベクトル  : {timing.get('cand_vec_sec', 0):.2f}"
          f"  （hit {timing.get('cache_hits', 0)} / miss {timing.get('cache_misses', 0)}）")
    print(f"  (d) スコアリング  : {timing.get('scoring_sec', 0):.2f}")
    print()

    # results/: 一時保存（gitignore 対象）
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = RESULTS_DIR / f"solo_{me_name}_{MODEL_TAG}_{ts}.json"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # snapshots/: 永続保存（git 追跡対象）。同じ seeker×モデルは上書きせず日時を付ける。
    SNAPSHOTS_DIR = RESULTS_DIR.parent / "snapshots"
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snap_name = f"solo_{me_name}_{MODEL_TAG}_{ts}.json"
    snap_path = SNAPSHOTS_DIR / snap_name
    with open(snap_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"結果保存（一時）: {tmp_path}")
    print(f"結果保存（永続）: {snap_path}")
    print()
    print("git でスナップショットを記録するには:")
    print(f"  git add eval/snapshots/{snap_name}")
    print(f'  git commit -m "snapshot: {me_name} × {MODEL_TAG} マッチング結果"')
    print("  git push origin exp/embedding-model-eval")



if __name__ == "__main__":
    main()
