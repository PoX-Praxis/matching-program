"""pytest 共通の前処理。

app.py は本番（POX_DEBUG!=1）で POX_EMAIL_SALT 未設定なら起動を止める（指示書26 §1-3）。
テストは POX_DEBUG を明示的に切り替えるものがあるため、ここで固定のテスト用ソルトを
用意しておき、app の import が起動ガードで落ちないようにする（本番同様「ソルトあり」）。
POX_EMAIL_SALT は POX_SECRET_KEY とは独立の環境変数であることの確認も兼ねる。
"""
import os

os.environ.setdefault("POX_EMAIL_SALT", "test-insecure-email-salt")

# 削除の検証の legacy 境界（指示書45C §1-1）。本番は未設定なら起動しない。
# テストの DB には #113 以前の合意が無いので 0（＝legacy なし）を明示的に与える。
os.environ.setdefault("POX_LEGACY_BOUNDARY_SEQ", "0")

# 埋め込みのバックエンド（指示書55-2 B-1）。本番は stub では起動しない（183）。テストは外部の
# 推論サーバーを呼ばないため stub で動かす。本番の起動拒否を外す専用の変数（本番では設定しない）。
os.environ.setdefault("POX_TEST_ALLOW_STUB", "1")

# 力になれること（与え像）の公開の閾値（指示書64 §1-2）。本番は既定値（PR-64A のデプロイ日時）。テストは
# 壁時計に左右されないよう、いま作られた版を「閾値より後」として扱う。閾値より前を確かめるテストは個別に上書きする。
os.environ.setdefault("POX_OFFER_PUBLIC_SINCE", "2000-01-01T00:00:00+00:00")
