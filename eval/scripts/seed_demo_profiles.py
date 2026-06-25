"""
GitHub プロフィール（eval/github_profiles.jsonl）をローカル DB に投入して
デモ環境を作るシードスクリプト

使い方:
    cd matching-program-exp
    python eval/scripts/seed_demo_profiles.py

    # すでに投入済みのプロフィールをリセットしてやり直す場合:
    python eval/scripts/seed_demo_profiles.py --reset

実行後:
    python app.py  → ブラウザで http://localhost:5000 を開く
    → 100件のデモプロフィールに対してマッチングを体験できる

注意:
    - ローカルの pox.db にのみ書き込む（本番 Render には一切影響しない）
    - github_login を user_id のプレフィックスに使用（重複しない）
    - マッチングは ANTHROPIC_API_KEY があれば Claude 判定、なければ bigram デモ判定
"""
import sys, json, pathlib, uuid

# プロジェクトルートの src を import パスに追加
ROOT = pathlib.Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import save_profile

POOL_FILE = pathlib.Path(__file__).parent.parent / "github_profiles.jsonl"
DB_PATH   = str(ROOT / "pox.db")
ID_PREFIX = "gh_"


def load_pool():
    if not POOL_FILE.exists():
        print(f"ERROR: {POOL_FILE} が見つかりません。")
        print("先に eval/scripts/fetch_github_profiles.py を実行してください。")
        sys.exit(1)
    pool = []
    with open(POOL_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pool.append(json.loads(line))
    return pool


def github_profile_to_seeker(p: dict) -> tuple[str, dict]:
    """
    github_profiles.jsonl の1行 → (user_id, seeker_dict) に変換。
    seeker_dict は PoX v4 形式（日本語キー）。
    """
    login    = p["github_login"]
    user_id  = f"{ID_PREFIX}{login}"
    name     = p.get("display_name") or login

    # v4 seeker 形式（normalize_to_seeker が受け付ける形）
    seeker = {
        "schema_version": "v4",
        "id":    login,           # ハンドル（表示名として使われる）
        "意志":  p.get("will_text", ""),
        "現状": {
            "持っているもの":   p.get("state_have", ""),
            "できること_型":    p.get("state_can_type", ""),
            "縛られているもの": p.get("state_bound", ""),
            "未分類":           p.get("state_unsorted", ""),
        },
        "_meta": {
            "schema_version": "v4",
            "source":         "github_demo",
            "github_login":   login,
            "display_name":   name,
        },
    }
    return user_id, seeker


def get_existing_demo_ids(db_path: str) -> set:
    """DB に投入済みのデモプロフィール ID を返す。"""
    import sqlite3
    try:
        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT user_id FROM profiles WHERE user_id LIKE ?",
            (f"{ID_PREFIX}%",)
        ).fetchall()
        con.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


def reset_demo_profiles(db_path: str):
    """DB からデモプロフィール（gh_ プレフィックス）を全削除する。"""
    import sqlite3
    con = sqlite3.connect(db_path)
    n_profiles = con.execute(
        "DELETE FROM profiles WHERE user_id LIKE ?", (f"{ID_PREFIX}%",)
    ).rowcount
    n_seekers = con.execute(
        "DELETE FROM seekers WHERE id LIKE ?", (f"{ID_PREFIX}%",)
    ).rowcount
    con.commit()
    con.close()
    print(f"リセット完了: profiles {n_profiles}件 / seekers {n_seekers}件 削除")


def main():
    reset = "--reset" in sys.argv
    pool  = load_pool()
    print(f"プロフィール数: {len(pool)} 件")

    if reset:
        reset_demo_profiles(DB_PATH)

    existing = get_existing_demo_ids(DB_PATH)
    print(f"投入済み: {len(existing)} 件")

    inserted = 0
    skipped  = 0

    for p in pool:
        user_id, seeker = github_profile_to_seeker(p)

        if user_id in existing:
            skipped += 1
            continue

        try:
            save_profile(user_id, seeker, db_path=DB_PATH)
            inserted += 1
            login = p["github_login"]
            will  = p.get("will_text", "")[:40]
            print(f"  [{inserted}] {login}: {will}...")
        except Exception as e:
            print(f"  ERROR ({p.get('github_login','?')}): {e}")

    print(f"\n完了: 新規投入 {inserted} 件 / スキップ {skipped} 件")
    print(f"DB: {DB_PATH}")
    print()
    print("次のコマンドでアプリを起動してください:")
    print("  python app.py")
    print("ブラウザで http://localhost:5000 を開くとマッチングを体験できます。")


if __name__ == "__main__":
    main()
