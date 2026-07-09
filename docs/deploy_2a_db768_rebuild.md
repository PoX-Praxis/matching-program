# 手順書① — DB フル768化：profile_vectors 再作成（束2a）

対象: 本番（Render / `pox-web` + Postgres）。`profile_vectors` を **フル768専用**へ移行する。
束2a で `_256` 列・256次元 HNSW を廃止し、照合はフル次元総当たり（スケール戦略 stage1）へ切替済み。
`schema_v4.init_v4()` はスキーマ差分（次元・列構造）を検出し、破壊的再作成が必要な場合のみ
**0件確認 + フラグ**を要求して停止する。データを黙って壊さない設計。

> ⚠️ 破壊的操作は「本番DBのdropは0件確認 + `POX_ALLOW_VECTOR_TABLE_REBUILD=1` のときのみ」。
> ベクトルは登録フローで再生成できる派生データなので原則0件から作り直す。**profiles_v4 /
> derived_necessity の本体データは触らない**（再作成対象は profile_vectors 等のベクトル表のみ）。

---

## 0. 事前確認（変更なし・読むだけ）

Render Dashboard → Postgres → Connect → psql、または `pox-web` の Shell から:

```sql
-- profile_vectors の現在の列と次元を確認（_256 列が残っているか / vector 次元）
SELECT column_name, data_type FROM information_schema.columns
 WHERE table_name = 'profile_vectors' ORDER BY ordinal_position;

-- 行数（再作成の可否判定。0 なら安全に作り直せる）
SELECT count(*) FROM profile_vectors;

-- 本体データは温存対象。件数だけ控える（中身は出さない）
SELECT count(*) FROM profiles_v4;
SELECT count(*) FROM derived_necessity;
```

- `profile_vectors` が **0件** → そのまま再作成へ進める。
- **1件以上**残っている旧次元のベクトルがある → 下記「行が残っている場合」を先に実施。

---

## 1. pox-web 環境変数（embedding 設定）

Render → `pox-web` → Environment。**この段階では `POX_EMBED_BACKEND` は `stub` のまま**
（実モデルへの切替＝Nomic 接続は束2d。ここでは DB スキーマとホストの準備だけ行う）。

| 変数 | 値 | 備考 |
|------|-----|------|
| `POX_EMBED_MODEL_TAG` | `nomic-emb-v2` | プールの model_tag。Nomic サービスの `NOMIC_MODEL_TAG` と一致必須 |
| `POX_EMBED_FULL_DIM` | `768` | Nomic v2 の実次元（`/health` の `dim` で確定した値。手順書②参照） |
| `POX_EMBED_BACKEND` | `stub`（据え置き） | 束2d で `nomic` に切替。stub でも768次元で配管検証できる |

> `MODEL_DIMS['nomic-emb-v2']` が未登録でも `POX_EMBED_FULL_DIM=768` が最優先されるため動く。
> 768が実次元と異なると `_nomic_encode` が次元不一致で弾く（推測で埋めない設計）。

---

## 2. 再作成フラグを付けて init_v4 を実行

`profile_vectors` が0件であることを **§0 で確認済み**であること。

Render → `pox-web` → Shell（またはデプロイ時に一度だけ流す）:

```bash
# 0件を確認済みのときだけフラグを付ける
POX_ALLOW_VECTOR_TABLE_REBUILD=1 python -c "import sys; sys.path.insert(0,'src'); import schema_v4; schema_v4.init_v4()"
```

期待するログ:

```
[schema_v4] 再作成: profile_vectors を drop→再作成します。理由: ...（0件確認済・POX_ALLOW_VECTOR_TABLE_REBUILD=1）
```

フラグ無しで実行すると、差分検出時に

```
[schema_v4] 停止: profile_vectors は再作成が必要（...）だが POX_ALLOW_VECTOR_TABLE_REBUILD=1 が未設定。
```

と表示して `sys.exit` する（＝黙って壊さない安全側）。

> `app.py` 起動時にも `init_v4()` は呼ばれるが、`SystemExit` を握りつぶす実装なので
> **通常デプロイでは破壊的再作成は起こらない**。再作成は上記の明示コマンドでのみ行う。

---

## 3. 再作成の確認

```sql
-- _256 列が消え、フル列だけになっていること
SELECT column_name FROM information_schema.columns
 WHERE table_name='profile_vectors' AND column_name LIKE '%\_256' ESCAPE '\';
-- → 0 行が正しい

-- generation_status / generation_error 列が profiles_v4 に存在すること（束2b 非同期用）
SELECT column_name FROM information_schema.columns
 WHERE table_name='profiles_v4' AND column_name IN ('generation_status','generation_error');
-- → 2 行

-- derived_necessity の履歴保存索引（有効行の一意性）
SELECT indexname FROM pg_indexes WHERE tablename='derived_necessity' AND indexname='uq_dn_active';
-- → 1 行
```

登録 → `GET /v4/seekers/<id>/status` が `ready` になり、`profile_vectors` に768次元が
入ること（`POX_EMBED_BACKEND=stub` でも768ベクトルは生成される）を1件で確認する。

---

## 4. フラグの除去（重要）

再作成が済んだら **`POX_ALLOW_VECTOR_TABLE_REBUILD` を Environment から削除**する。
残したままだと、将来スキーマ差分が出た際に本番デプロイで意図せず drop が走る余地を残す。

---

## 行が残っている場合（profile_vectors が1件以上）

旧次元（256混在）のベクトルは新照合と非互換なので破棄して作り直す。**ベクトルは派生物**で
`profiles_v4` / `derived_necessity` から再生成できる。

```sql
-- 監査のため件数だけ控えてから空にする（本体 profiles_v4 は消さない）
TRUNCATE profile_vectors;
SELECT count(*) FROM profile_vectors;  -- 0 を確認
```

その後 §2 のフラグ付き `init_v4()` を実行 → §3 確認 → §4 フラグ除去。
既存プロフィールのベクトルは、各 seeker の登録/更新（`/v4/seekers`）または照合時の
遅延移行（`/v4/match` の `ensure_migrated`）で順次再生成される。
