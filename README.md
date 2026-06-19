# PoX 接続層（②マッチング・メカニズム）— Claude Code 引き継ぎリポジトリ

> **Claude Code へ：まず `spec/matching_spec_v0.6.md` を読んでください。** それ一枚でこのプロジェクトの設計判断と現在地が分かる自己完結ブリーフです。本READMEは地図、v0.6が本文です。

## 0. これは何か（30秒）
人間の「文脈→目標→手段→その手段を担える人材」という列を、LLMの中核操作（attention＝重み付き集約／予測＝系列補完）の発想で照合し、「目的を共有でき、互いの不足を補える人／コミュニティ」を高精度に接続する仕組み（PoXのマッチング・エンジン）。

全体プランは3段階で、**このリポジトリは②が担当範囲**：
1. ①構造化プロンプト … 別管理。v3まで完成（人を固定スキーマへ構造化し、本人合意で出力）。
2. **②接続層 … 本リポジトリ。完成（単一入口 `run_matching` まで通っている）。**
3. ③プロトタイプ・プラットフォーム … 次フェーズ。蓄積層＋接続UI。②を `import` して使う。

## 1. 現在地（v0.6時点で完成していること）
- 構造化情報（seeker）を入れると、必要像を生成し、二チャネル（類似＝対称／相補＝非対称）で照合し、根拠付きのマッチング候補者リストを返す `run_matching` が動く（`src/connection_layer.py`）。
- 入力規格は構造化プロンプトv3と相互確認済み（統合フェーズ完了）。
- 接続成否ラベルの取得方法を確定（相互承認トリガー）し、台帳スキーマ化（`schemas/connection_ledger.schema.json`）。
- 別人2名で異なる結果が出ることを確認＝特定個人に依存しない汎用設計。

## 2. ディレクトリ
```
src/        実行する本体
  connection_layer.py          ★②の単一入口 run_matching（③はこれを叩くだけ）
  matching_engine_live.py      実マッチャ（run_matchingの前身。参考）
  github_transition_builder.py 遷移テーブル構築器（要 GITHUB_TOKEN、本番でテーブルを実データ化）
  validate_and_convert.py      接続成否ログの検証＋7.1節の型への変換
schemas/
  connection_ledger.schema.json 接続成否ログ（承認後の記録形式。③の承認UIと繋ぐ）
spec/       正典（ここを読めば設計が分かる）
  matching_spec_v0.6.md        ★最初に読む。自己完結ブリーフ
  connection_ledger_spec_v1.md 接続成否ログの設計（人が読む版）
  notice_to_structuring_prompt_v2.md ①へ渡した入力要求
  reply_to_v3_FIX_phase_key.md ①v3サンプルへの確認結果
examples/
  run_demo.py                  ②を③の呼び方で実行するデモ
  ledger_samples.json          台帳のサンプルデータ
archive/    設計過程の記録（読まなくてよい。失敗の経緯＝設計判断の根拠）
```

## 3. 動かす
```bash
# 依存
pip install jsonschema

# ②のデモ（API無し: judge_fn でLLM代行を模擬。順位が出る）
python examples/run_demo.py

# 本番でLLMに自動採点させる場合（少人数ならこれで足りる＝仕様5.5節）
export ANTHROPIC_API_KEY=...      # 秘密情報。コミットしないこと
# run_matching(..., judge_fn=None) で実Claude呼び出しになる

# 接続成否ログの検証＋変換
python src/validate_and_convert.py examples/ledger_samples.json

# 遷移テーブルを実データ化（本番。GitHub公開データ読み取りは無料）
export GITHUB_TOKEN=...           # 秘密情報。コミットしないこと
python src/github_transition_builder.py --out transition_table.json
```

## 4. ②の使い方（③から呼ぶときの契約）
```python
from connection_layer import run_matching

result = run_matching(
    seeker={"意志":..., "求めている":..., "能力":..., "フェーズ":"mvp"},  # v3の出力そのまま
    candidate_pool=[{"id":..., "profile":...}, ...],                      # ③が共有DBから参照して渡す
    want="complement",   # "complement"(相補)/"similar"(類似)/"balanced"
)
# result["ranking"] = [{rank, id, score, comp, sim, comp_via, reason}, ...] を③が表示
```

## 5. 次にやること（③フェーズ）
1. 共有DB（最初はSQLite/JSONで十分）に、利用者の seeker を蓄積する投稿フォーム。
2. マッチング要求時にDBから候補プールを参照し、`run_matching` に渡し、`ranking` を画面表示。
3. 候補が相互承認したら、`schemas/connection_ledger.schema.json` の形で台帳に1件記録（承認UI）。
   - これが接続成否ラベル（弱い正解＝接続成立／強い正解＝行動変化）を貯める入口。
4. 母数（登録ユーザー）を集めるのは現実の活動（エンジン外）。②③とは別レーン。

## 6. 残課題（②内部・③とは別）
- 遷移テーブルが現状GitHub文脈に偏る（必要像が実装者系に寄りやすい）。本番は `github_transition_builder.py` の実データ＋蓄積後の接続成否ログで精度と幅を広げる（仕様8章の二段構造）。
- 多人数化したら(1)ベクトル化層を既製embeddingに差し替える（仕様5.5節。少人数はLLM代行で足りる）。

## 7. 秘密情報の扱い（厳守）
`ANTHROPIC_API_KEY` / `GITHUB_TOKEN` はコードにもリポジトリにも書かない。環境変数で渡す。`.gitignore` が `.env` 等を除外済み。公開リポジトリにする場合は特に注意。
