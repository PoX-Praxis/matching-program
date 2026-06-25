"""
GitHub 公開プロフィールを取得し Claude で PoX v4 形式に構造化するスクリプト

使い方（Windows PC 上で実行）:
    set GITHUB_TOKEN=ghp_xxxx
    set ANTHROPIC_API_KEY=sk-ant-xxxx
    cd matching-program-exp
    # 追加で 500 件構造化する場合:
    set GITHUB_PROFILE_COUNT=500
    python eval/scripts/fetch_github_profiles.py

    （PowerShell の場合）
    $env:GITHUB_TOKEN = "ghp_xxxx"
    $env:ANTHROPIC_API_KEY = "sk-ant-xxxx"
    $env:GITHUB_PROFILE_COUNT = "500"
    python eval/scripts/fetch_github_profiles.py

出力: eval/github_profiles.jsonl（既存ファイルに追記）
  1行1JSON: {"github_login":"...", "display_name":"...", "bio":"...",
             "will_text":"...", "state_have":"...", "state_can_type":"...",
             "state_bound":"...", "state_unsorted":"..."}

調整できる環境変数:
  GITHUB_PROFILE_COUNT   今回新たに構造化する目標件数（既定 100）
  GITHUB_SEARCH_MAX_PAGES 1クエリあたりの取得ページ数（既定 5・per_page=30）
  GITHUB_OVERSAMPLE      フィルタ脱落を見込んだ過剰収集倍率（既定 2.5）

設計:
  - GitHub Search API で多様なユーザーを収集（日本語/英語混在）
  - フォロワーを帯域で分割し、毎回トップの有名人を再取得しないようにする
  - bio が空・リポジトリ数が少ないユーザーは除外
  - 再実行時は出力済みログインをスキップ（レジューム可能・既存90件は自動スキップ）
  - GITHUB_TOKEN なしでも動作するが rate limit が 60 req/h になる
"""
import os, json, time, sys, pathlib, urllib.request, urllib.error, re

# ── 設定 ──────────────────────────────────────────────────────────────────────
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TARGET_COUNT     = int(os.environ.get("GITHUB_PROFILE_COUNT", "100"))
MIN_BIO_LEN      = 20   # bio がこれより短いユーザーはスキップ
MIN_REPOS        = 5    # public_repos がこれより少ないユーザーはスキップ
# 1クエリあたり何ページ取るか（per_page=30）。深く掘るほど新規（=低フォロワー層）が増える。
SEARCH_MAX_PAGES = int(os.environ.get("GITHUB_SEARCH_MAX_PAGES", "5"))
# 過剰収集の倍率。bio/repos フィルタで脱落する分を見込み、目標の何倍の候補を集めるか。
OVERSAMPLE       = float(os.environ.get("GITHUB_OVERSAMPLE", "2.5"))

OUTPUT_FILE = pathlib.Path(__file__).parent.parent / "github_profiles.jsonl"

# ── 多様性を確保する検索クエリ群 ──────────────────────────────────────────────
# 設計方針:
#   - フォロワーを「帯域（range）」で分割し、毎回トップの有名人を再取得しないようにする
#     （例: 100..300 と 30..100 は別集合 → 新規が増える）。500件追加に必要な裾野を確保。
#   - エンジニア偏重（均質プール問題）を緩和するため、役割・ドメインの軸を増やす
#     （研究/デザイン/社会課題/教育/起業/アート/学生 など）。
#   - 地域・言語も広げて多様性を底上げ。
SEARCH_QUERIES = [
    # ── 日本：フォロワー帯域で分割（重複回避しつつ裾野まで） ──
    "location:Japan followers:200..500",
    "location:Japan followers:100..200",
    "location:Japan followers:50..100",
    "location:Japan followers:30..50",
    "location:Tokyo followers:50..200",
    "location:Tokyo followers:20..50",
    "location:Osaka followers:20..100",
    "location:Kyoto followers:15..100",
    "location:Fukuoka followers:15..100",
    "location:Nagoya followers:15..100",
    "location:Sapporo followers:10..100",
    "location:Sendai followers:10..100",
    # ── 日本：言語軸 ──
    "location:Japan language:Python followers:30..150",
    "location:Japan language:JavaScript followers:30..150",
    "location:Japan language:TypeScript followers:30..150",
    "location:Japan language:Go followers:20..150",
    "location:Japan language:Rust followers:20..150",
    "location:Japan language:Ruby followers:20..150",
    "location:Japan language:Swift followers:20..150",
    # ── 役割・ドメイン軸（非エンジニアを掘る：均質性を崩す）──
    "researcher in:bio location:Japan",
    "PhD in:bio location:Japan",
    "designer in:bio location:Japan followers:>20",
    "artist in:bio location:Japan",
    "writer in:bio location:Japan",
    "educator OR teacher in:bio location:Japan",
    "student in:bio location:Japan followers:>20",
    "founder in:bio location:Japan followers:>30",
    "startup in:bio location:Japan followers:>30",
    "product manager in:bio location:Japan",
    "data scientist in:bio location:Japan followers:>20",
    "social in:bio location:Japan",
    "nonprofit OR NPO in:bio",
    "music OR game OR creative in:bio location:Japan followers:>30",
    # ── 社会課題・スタートアップ（英語bio・海外含む）──
    "social+impact in:bio followers:50..300",
    "startup founder in:bio followers:100..500",
    "open+source in:bio location:Japan followers:30..200",
    "indie+developer in:bio followers:30..300",
    "civic+tech in:bio",
    "accessibility in:bio followers:>20",
]

# ── GitHub API ────────────────────────────────────────────────────────────────
def _gh_headers():
    h = {"Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def _gh_get(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_gh_headers())
            with urllib.request.urlopen(req, timeout=15) as r:
                remaining = int(r.headers.get("X-RateLimit-Remaining", 999))
                if remaining < 5:
                    reset_at = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
                    wait = max(0, reset_at - time.time()) + 2
                    print(f"  [rate-limit] {wait:.0f}s 待機...")
                    time.sleep(wait)
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (403, 429):
                wait = 60 * (attempt + 1)
                print(f"  [rate-limit {e.code}] {wait}s 待機...")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    return None


def search_users(query, per_page=30, max_pages=SEARCH_MAX_PAGES):
    logins = []
    for page in range(1, max_pages + 1):
        q = urllib.parse.quote(query)
        url = f"https://api.github.com/search/users?q={q}&per_page={per_page}&page={page}&sort=followers"
        data = _gh_get(url)
        if not data or not data.get("items"):
            break
        logins.extend(item["login"] for item in data["items"])
        time.sleep(1.2)  # Search API: 10 req/min (authenticated)
    return logins


def get_user_detail(login):
    return _gh_get(f"https://api.github.com/users/{login}")


def get_user_repos(login, top_n=5):
    data = _gh_get(
        f"https://api.github.com/users/{login}/repos"
        f"?sort=stars&direction=desc&per_page={top_n}&type=owner"
    )
    if not data:
        return []
    return [
        {"name": r["name"], "description": r.get("description") or "", "stars": r.get("stargazers_count", 0)}
        for r in data if not r.get("fork")
    ]


def get_profile_readme(login):
    """profile README（{login}/{login} リポジトリの README.md）を取得する。"""
    data = _gh_get(f"https://api.github.com/repos/{login}/{login}/readme")
    if not data:
        return ""
    import base64
    content = data.get("content", "")
    try:
        text = base64.b64decode(content).decode("utf-8", errors="replace")
        # HTML タグと Markdown リンクを除去して生テキストに
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        return text[:2000].strip()
    except Exception:
        return ""


# ── Claude API（PoX構造化）────────────────────────────────────────────────────
STRUCTURE_PROMPT = """\
あなたは PoX（Proof of X）マッチングプラットフォームの登録支援 AI です。
以下は GitHub の公開プロフィール情報です。この情報を読み取り、
PoX v4 スキーマの5つのフィールドを JSON で出力してください。

## GitHub プロフィール情報
{profile_text}

## 出力形式（JSON のみ。説明文は不要）
{{
  "will_text": "この人が本当に実現しようとしていること・大切にしていることを1〜3文で（推測可）",
  "state_have": "現在持っているスキル・経験・資産を具体的に（複数ある場合は読点で区切る）",
  "state_can_type": "この人の動き方・仕事の型（例: 技術で作る人、研究する人、つなぐ人、発信する人）",
  "state_bound": "所在地・所属・時間的制約など現状の縛り（情報がなければ空文字）",
  "state_unsorted": "他に読み取れる特徴・文脈（情報がなければ空文字）"
}}

## 注意
- bio や README の言語にかかわらず日本語で出力する
- 情報が薄い項目は空文字にする（推測で埋めすぎない）
- 実在人物の個人情報（連絡先等）は含めない
- JSON 以外の文字は出力しない
"""


def structure_with_claude(user, repos, readme):
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY が設定されていません。")

    bio = user.get("bio") or ""
    name = user.get("name") or user["login"]
    location = user.get("location") or ""
    company = user.get("company") or ""
    blog = user.get("blog") or ""

    repo_lines = "\n".join(
        f"  - {r['name']} (★{r['stars']}): {r['description']}" for r in repos
    ) or "  （なし）"

    profile_text = f"""名前: {name}
ログイン: {user['login']}
bio: {bio}
所在地: {location}
所属: {company}
ブログ/URL: {blog}
主なリポジトリ:
{repo_lines}
"""
    if readme:
        profile_text += f"\nプロフィール README（抜粋）:\n{readme[:800]}"

    prompt = STRUCTURE_PROMPT.format(profile_text=profile_text)

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())

    raw = resp["content"][0]["text"].strip()
    # JSON ブロック抽出
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError(f"Claude が JSON を返しませんでした: {raw[:200]}")
    return json.loads(m.group(0))


# ── メイン ────────────────────────────────────────────────────────────────────
def load_done_logins():
    done = set()
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    done.add(json.loads(line)["github_login"])
                except Exception:
                    pass
    return done


def main():
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY が未設定です。", file=sys.stderr)
        sys.exit(1)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    done = load_done_logins()
    print(f"既処理: {len(done)} 件 / 目標: {TARGET_COUNT} 件")

    # ── ユーザー収集（クエリをローテーション）──
    # フィルタ（bio/repos）で一定割合が脱落するため、目標の OVERSAMPLE 倍まで候補を集める。
    # 収集段階で done（処理済み）は seen に入れて二重取得しない＝スキップ。
    collected_logins = list(done)  # 順序保持
    seen = set(done)
    want_candidates = int(TARGET_COUNT * OVERSAMPLE)

    for query in SEARCH_QUERIES:
        new_so_far = len(collected_logins) - len(done)
        if new_so_far >= want_candidates:
            break
        print(f"[search] {query}  (新規候補 {new_so_far}/{want_candidates})")
        logins = search_users(query)
        added = 0
        for login in logins:
            if login not in seen:
                seen.add(login)
                collected_logins.append(login)
                added += 1
        print(f"         +{added} 件（うち既処理スキップ済み）")

    new_logins = [l for l in collected_logins if l not in done]
    print(f"新規候補: {len(new_logins)} 件（目標処理数 {TARGET_COUNT} / 過剰収集上限 {want_candidates}）")

    processed = 0
    skipped = 0

    with open(OUTPUT_FILE, "a", encoding="utf-8") as out:
        for login in new_logins:
            if processed >= TARGET_COUNT:
                break

            print(f"[{processed+1}/{TARGET_COUNT}] {login} ...", end=" ", flush=True)
            try:
                user = get_user_detail(login)
                if not user:
                    print("skip (404)")
                    skipped += 1
                    continue

                bio = (user.get("bio") or "").strip()
                repos_count = user.get("public_repos", 0)

                if len(bio) < MIN_BIO_LEN:
                    print(f"skip (bio 短: {len(bio)}文字)")
                    skipped += 1
                    continue
                if repos_count < MIN_REPOS:
                    print(f"skip (repos 少: {repos_count})")
                    skipped += 1
                    continue

                repos = get_user_repos(login)
                time.sleep(0.5)
                readme = get_profile_readme(login)
                time.sleep(0.5)

                structured = structure_with_claude(user, repos, readme)
                time.sleep(0.3)  # Claude API

                record = {
                    "github_login":   login,
                    "display_name":   user.get("name") or login,
                    "bio":            bio,
                    "will_text":      structured.get("will_text", ""),
                    "state_have":     structured.get("state_have", ""),
                    "state_can_type": structured.get("state_can_type", ""),
                    "state_bound":    structured.get("state_bound", ""),
                    "state_unsorted": structured.get("state_unsorted", ""),
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                print(f"ok (will: {record['will_text'][:40]}...)")
                processed += 1

            except Exception as e:
                print(f"error: {e}")
                skipped += 1
                time.sleep(2)

    total = len(done) + processed
    print(f"\n完了: 新規 {processed} 件処理 / スキップ {skipped} 件 / 合計 {total} 件")
    print(f"出力: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
