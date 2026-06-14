#!/usr/bin/env python3
"""
GitHub 遷移テーブル構築器 v1  (結論A / GitHub版の完成形)
=========================================================
確定した「接続イベントの型」をGitHubから大量集計し、
  P( 状態変化が起きた参加者の特徴 | 接続前のフェーズ )
という確率つき遷移テーブルを出力する。これは仕様書7章の
手書き表をデータで裏打ち・確率化したもの = 結論Aの成果物。

【接続イベントの型(3列)】
  ① 接続前の文脈・フェーズ : star規模＋経過で近似 (①〜④)
  ② 接続した相手の特徴     : 新規コントリビュータの followers/repos/専門
  ③ その後の状態変化       : 参加の前後でのstar増・活動変化 (※因果でなく代理指標)

【使い方】
  export GITHUB_TOKEN=ghp_xxxx        # 無料の個人トークン。無くても動くが60/時で実用にならない
  python3 github_transition_builder.py --queries "language:python stars:50..500" "language:python stars:5000..50000" --repos-per-query 20

【重要な但し書き】
  ③の状態変化は PoX の「行動が変わった瞬間」のネット上の代理指標(プロキシ)であり、
  因果の証明ではない。自前ログ(行動変化の実記録)が貯まったら③は本物に置き換える。
"""
import sys, os, json, time, argparse, urllib.request, urllib.error
from urllib.parse import urlencode
from collections import defaultdict
from datetime import datetime, timezone

API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

# ---- フェーズ定義: ここが7章の表に対応する。star閾値は調整可能な設計パラメータ ----
PHASES = [
    # (ラベル, star上限, 7章対応)
    ("①アイデア/初期",        50,     "壁打ち・構造化できる人"),
    ("②MVP/機能拡張",        500,    "作れる人"),
    ("③初期検証/伸長",       5000,   "マーケ・営業・初期ユーザー"),
    ("④スケール",            float("inf"), "組織・オペレーション"),
]
def phase_of(stars, n_contrib):
    if n_contrib <= 1 and stars < 50:
        return PHASES[0][0]
    for label, ub, _ in PHASES:
        if stars < ub:
            return label
    return PHASES[-1][0]

# ---- 参加者の特徴を離散バケットに(集計可能にするため) ----
def follower_bucket(f):
    if f is None: return "unknown"
    if f < 10:    return "fol:<10"
    if f < 100:   return "fol:10-100"
    if f < 1000:  return "fol:100-1k"
    return "fol:>1k"
def repo_bucket(r):
    if r is None: return "unknown"
    if r < 5:   return "repo:<5"
    if r < 30:  return "repo:5-30"
    return "repo:>30"

def gh(path, params=None, retries=3):
    url = API + path + (("?"+urlencode(params)) if params else "")
    headers = {"Accept":"application/vnd.github+json","User-Agent":"pox-matching-poc"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r), dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code == 403:  # rate limit
                reset = e.headers.get("X-RateLimit-Reset")
                rem   = e.headers.get("X-RateLimit-Remaining")
                if rem == "0" and reset:
                    wait = max(0, int(reset) - int(time.time())) + 2
                    if wait < 120:  # 短ければ待つ、長ければ諦めて報告
                        time.sleep(wait); continue
                return {"_error":403,"_body":"rate limited","_reset":reset}, dict(e.headers)
            if e.code == 404:
                return {"_error":404}, {}
            time.sleep(1.5*(attempt+1))
    return {"_error":"max_retries"}, {}

def search_repos(query, n):
    out=[]; page=1
    while len(out) < n:
        d,_ = gh("/search/repositories", {"q":query,"sort":"stars","order":"desc",
                                          "per_page":min(100,n-len(out)),"page":page})
        if d.get("_error"):
            print(f"  [search error] {d}", file=sys.stderr); break
        items=d.get("items",[])
        if not items: break
        out.extend(items); page+=1
        if len(items)<100: break
        time.sleep(1)
    return out[:n]

def list_commits(owner, repo, max_commits=300):
    out=[]; page=1
    while len(out) < max_commits:
        d,_ = gh(f"/repos/{owner}/{repo}/commits", {"per_page":100,"page":page})
        if isinstance(d,dict) and d.get("_error"):
            break
        if not d: break
        out.extend(d); page+=1
        if len(d)<100: break
        time.sleep(0.2)
    return out

def first_appearances(commits):
    """各コントリビュータの初登場(=新規接続)を古い順で返す。"""
    seen={}
    for c in commits:  # commitsは新しい順
        a=c.get("author")
        login=a["login"] if a else None
        if not login: continue
        date=c.get("commit",{}).get("author",{}).get("date")
        seen[login]=date  # 最後に上書き=最古に近い(新しい順走査のため)
    return sorted(seen.items(), key=lambda kv: kv[1] or "")

def state_change_proxy(commits, join_date):
    """③状態変化の代理: 参加日より後のコミット数 / 前のコミット数 で活性化を近似。
       >1 なら参加後に活動が増えた = ポジティブな状態変化の代理。"""
    if not join_date: return None
    before=after=0
    for c in commits:
        d=c.get("commit",{}).get("author",{}).get("date")
        if not d: continue
        if d < join_date: before+=1
        elif d > join_date: after+=1
    if before==0: return None
    return round(after/before, 2)

_profile_cache={}
def profile(login):
    if login in _profile_cache: return _profile_cache[login]
    u,_=gh(f"/users/{login}")
    p={"followers":u.get("followers"),"public_repos":u.get("public_repos")} if not u.get("_error") else {}
    _profile_cache[login]=p; time.sleep(0.1)
    return p

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--queries", nargs="+",
                    default=["language:python stars:50..500","language:python stars:5000..50000"])
    ap.add_argument("--repos-per-query", type=int, default=8)
    ap.add_argument("--max-contributors", type=int, default=10)
    ap.add_argument("--out", default="transition_table.json")
    args=ap.parse_args()

    print(f"認証: {'あり(5000/h)' if TOKEN else 'なし(60/h・実用外)'}\n")

    # 集計器: (phase, follower_bucket, repo_bucket) -> 状態変化代理値のリスト
    agg=defaultdict(list)
    events_log=[]

    for q in args.queries:
        print(f"### query: {q}")
        repos=search_repos(q, args.repos_per_query)
        print(f"  取得リポジトリ: {len(repos)}")
        for it in repos:
            owner=it["owner"]["login"]; name=it["name"]; stars=it["stargazers_count"]
            commits=list_commits(owner,name)
            if not commits: continue
            apps=first_appearances(commits)
            for i,(login,jdate) in enumerate(apps[:args.max_contributors]):
                ph=phase_of(stars, i+1)
                prof=profile(login)
                fb=follower_bucket(prof.get("followers"))
                rb=repo_bucket(prof.get("public_repos"))
                sc=state_change_proxy(commits, jdate)
                key=(ph,fb,rb)
                if sc is not None:
                    agg[key].append(sc)
                events_log.append({"repo":f"{owner}/{name}","phase":ph,"who":login,
                                   "follower_bucket":fb,"repo_bucket":rb,"state_change_proxy":sc})
            time.sleep(0.3)

    # ---- 確率/期待値テーブル化 ----
    table=[]
    for (ph,fb,rb),vals in sorted(agg.items()):
        pos=sum(1 for v in vals if v>1.0)
        table.append({
            "phase":ph,"follower_bucket":fb,"repo_bucket":rb,
            "n":len(vals),
            "mean_state_change":round(sum(vals)/len(vals),3),
            "p_positive_change":round(pos/len(vals),3),  # 参加後に活動増となった割合
        })
    table.sort(key=lambda r:(r["phase"], -r["p_positive_change"]))

    result={"_note":"state_change is a PROXY for PoX behavior-change, not causal proof.",
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "auth": bool(TOKEN), "transition_table":table, "n_events":len(events_log)}
    with open(args.out,"w") as f: json.dump(result,f,ensure_ascii=False,indent=2)

    print(f"\n=== 遷移テーブル(P:参加後に活動が増えた割合) ===")
    print(f"{'phase':<16}{'followers':<12}{'repos':<10}{'n':<5}{'mean':<7}{'p_pos'}")
    for r in table:
        print(f"{r['phase']:<16}{r['follower_bucket']:<12}{r['repo_bucket']:<10}"
              f"{r['n']:<5}{r['mean_state_change']:<7}{r['p_positive_change']}")
    print(f"\n出力: {args.out}  / 接続イベント総数: {len(events_log)}")

if __name__=="__main__":
    main()
