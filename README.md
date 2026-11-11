# PoX matching-program

PoX（Proof of X）の照合と接続のプラットフォーム。

- 人は、自分の目的と、目的ごとの「求めている相手（必要像）」「力になれること（与え像）」を言語化する
- PoX は、互いに補い合える相手を照合し、つながった事実を記録する

思想と背景は Notion にある。このリポジトリには、実装・判断・現状を置く（`docs/OPERATIONS.md` §1）。

## まず読むもの

| 文書 | 中身 |
|---|---|
| `docs/status.md` | いまの現状（1 ページ） |
| `docs/OPERATIONS.md` | 運用の決まり（指示書・報告・判断・計測の置き方） |
| `docs/decisions/` | 設計上の判断（一覧は `docs/decisions/README.md`） |
| `docs/status-report.md` | 詳しい棚卸し（時系列・構成・機能・状態・矛盾） |
| `CLAUDE.md` | AI が作業するときの決まりとセキュリティの制約 |

## 本体の構成

- **アプリ:** Flask 1 本（`app.py`）と、`src/` のモジュール。画面は `templates/`（Jinja）
- **本番:** Render の Web サービス `pox-web` と、Postgres（pgvector）
  - 設定は `render.yaml`。秘密の値は Render のダッシュボードで手入力し、リポジトリには書かない
- **埋め込み:** `nomic-embed-text-v2-moe`（タグ `nomic-emb-v2`・768 次元）
  - 推論サーバーは `nomic_server/` を別に立て、`POX_NOMIC_ENDPOINT` で指す
- **台帳:** 追記専用のイベント台帳（`ledger_events`）。毎日の Merkle root（アンカー）を GitHub Actions と外部の cron から刻む

## 処理の流れ（v5）

1. **構造化:** 利用者が自分の AI に `docs/prompts/` のプロンプトを渡し、出てきた JSON を `/register` に貼る
   - 下書き（`/v4/drafts`）を経て確定する。確定では台帳に先に書く
   - サーバーは構造化のための LLM を呼ばない
2. **埋め込み（非対称の検索）:** PII をマスクしてから埋め込む
   - 必要像の文は query、与え像・現状は passage にする
   - 目的の向かう先どうしは symmetric で比べる
3. **判定:** 上層（目的の必須がすべて満たされるか・連言）と、共鳴の門で決める
   - 下層（要素ごと）も計算するが、表示は閉じている
4. **根拠:** 必要像の文と相手の文の対を引用して示す
   - スコアなどの数値は、画面と公開 API に出さない
5. **接続:** 申し出 → 承認 → DM。事実は台帳に残る

ほかに、コミュニティ・トーク（合意）・プロジェクト参加・軌跡・ハンドル・マジックリンク認証がある。

## ディレクトリ

| 場所 | 中身 |
|---|---|
| `app.py` | 全ルート・認証ゲート・非同期ジョブ・照合の応答 |
| `src/` | DB・受信の検証（`v5.py`）・埋め込み・照合（`matcher_v5.py`）・台帳・コミュニティ・認証など |
| `templates/` | 画面（`/about`・`/register`・`/mypage`・`/profile`・`/connect`・`/inbox` など） |
| `docs/prompts/` | 構造化プロンプト（唯一の正） |
| `docs/` | 運用・設計メモ・手順書・計測（`docs/measure/`） |
| `docs/instructions/`・`docs/reports/`・`docs/decisions/` | 指示書・報告・判断 |
| `docs/history/` | 時点の記録（いまの正ではない） |
| `spec/` | 旧い仕様と、受入項目とテストの対応表（`spec/test_item_map.md`） |
| `scripts/` | 運用のスクリプトと、手元で実行する計測（データは `scripts/probe_data/`） |
| `tests/` | pytest。CI は `.github/workflows/tests.yml` |
| `nomic_server/` | 埋め込みの推論サーバー（本番で使う） |
| `qwen3_server/`・`archive/`・`examples/`・`schemas/` | 旧い世代（いまは使っていない） |

## 手元で動かす

```sh
pip install -r requirements.txt
POX_DEBUG=1 python app.py      # http://127.0.0.1:5000
python -m pytest -q
```

- `POX_DEBUG=1` が無いと「本番」とみなす。ソルト・削除の検証の境界・埋め込みのモデルが未設定なら、起動しない
- `DATABASE_URL` が無いと SQLite（`pox.db`）で動く。v4・v5 の登録と照合は Postgres が要る（SQLite では 503 を返す）
- 埋め込みの既定は stub（意味の無い擬似ベクトル）。stub の間は照合の結果を出さない（`POX_DEBUG=1` を除く）
- 鍵や `DATABASE_URL` の値は、コードにもリポジトリにも書かない（`CLAUDE.md`）

## 旧い README

2026-06 の「②マッチング・メカニズム」の引き継ぎ文書は `docs/history/README_2026-06.md` に移した。
