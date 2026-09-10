# スキーマ変更を既存DBへ反映する仕組み

## 問題：`CREATE TABLE IF NOT EXISTS` は既存テーブルを変えない

`src/schema.py` の DDL は `CREATE TABLE IF NOT EXISTS` で書かれている。これは
**テーブルが存在しないときだけ**効く。すでに本番に存在するテーブルに対しては、
DDL 側で列を追加・削除・制約変更しても **一切反映されない**。

この穴が原因で、指示書26 で `email_enc` 列を DDL から消しても、本番 Postgres の
`auth_tokens` には `email_enc NOT NULL` が残り、`issue_token` の INSERT が

```
psycopg2.errors.NotNullViolation: null value in column "email_enc" ...
```

で落ちた。**DDL を変えるだけでは本番に届かない。**

## 解決：`schema.init()` で冪等に「収束」させる

`schema.init()` は Postgres 起動時（`app.py`）に毎回呼ばれる。ここで DDL 実行の後に、
既存テーブルを意図するスキーマへ寄せる冪等処理を走らせる。

```
init()
 ├─ CREATE TABLE IF NOT EXISTS ...        # 新規テーブルの作成
 ├─ _migrate_user_snapshots(con)          # 後付け列（schema_version 等）
 └─ _migrate_columns(con)                 # 列の追加/削除の収束（下記）
```

`_migrate_columns()` が使うヘルパ（`schema.py`）：

| ヘルパ | 役割 | 冪等性 |
|---|---|---|
| `_column_exists(con, table, col)` | 列の有無（PG=information_schema / SQLite=PRAGMA） | 参照のみ |
| `_drop_column_if_exists(con, table, col)` | あれば `DROP COLUMN`、無ければ何もしない | 何度でも安全 |
| `_add_column_if_missing(con, table, col, type)` | 無ければ `ADD COLUMN`、あれば何もしない | 何度でも安全 |

- Postgres / SQLite の両方で動く（分岐は各ヘルパ内に閉じる）。
- 列名・型は**コード内リテラルのみ**。外部入力を SQL に埋め込まない。
- SQLite の `DROP COLUMN` は 3.35.0(2021) 以降。古い版では失敗するが、新規 SQLite DB には
  対象列が無いため try/except で握りつぶしてよい（本番は Postgres）。

### 現在 `_migrate_columns` が収束させているもの

- **削除**: `auth_tokens.email_enc` / `auth_identities.email_enc`（指示書26）
- **追加**: `connection_requests` の `predicted_role` / `channel` / `match_run_id`
  （指示書18 §3。従来 `ledger.py` が **SQLite でしか** ALTER しておらず、Postgres に
  列が無いと `ledger.approve()` が落ちる同型バグだった）
- **追加**: `profiles.view_overrides` / `profiles.created_at`（従来 `db.py` が SQLite のみ自己修復）
- **追加**: `messages.attachment_url`（従来 `messages.py` が SQLite のみ自己修復）

> 補足: モジュール側に散在していた「自己修復 ALTER」（`db.py` / `ledger.py` /
> `messages.py` / `snapshots.py`）は SQLite 専用で、Postgres に非対称だった。
> それらを `schema.init()` に集約し、**両エンジンで同じ収束**が起きるようにした。
> 既存の自己修復は残してあるが（無害）、正となる収束点は `schema.init()`。
>
> `profiles_v4`（`schema_v4.py`）は `ADD COLUMN IF NOT EXISTS`（Postgres）で後付け列を
> 冪等に足しており、この問題は無い。

## 今後スキーマを変えるときの手順

1. `schema.py` の DDL（`_SQLITE_DDL` / `_PG_DDL` 両方）を更新する。**これは新規DB用。**
2. **既存DBへ届けるため、必ず `_migrate_columns()` にも収束操作を足す。**
   - 列を足す → `_add_column_if_missing(con, "表", "列", "型")`
   - 列を消す → `_drop_column_if_exists(con, "表", "列")`
   - 制約変更（NOT NULL 付与/解除など）→ 専用の冪等 ALTER を書く
3. 冪等テストを追加する（`tests/test_schema_migration.py` に倣う）：
   「旧スキーマを作る → `schema.init()` → 期待どおり収束 → もう一度 init しても壊れない」。
4. デプロイ後、`schema.init()` が起動時に走って収束する（Postgres）。ローカル SQLite は
   `scripts/init_schema.py` か、対象モジュールの遅延 DDL で反映される。

**鉄則：DDL を変えたら必ず `_migrate_columns()` にも対応を書く。**
DDL だけの変更は新規DBにしか効かない。
