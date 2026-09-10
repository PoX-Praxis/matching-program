"""pytest 共通の前処理。

app.py は本番（POX_DEBUG!=1）で POX_EMAIL_SALT 未設定なら起動を止める（指示書26 §1-3）。
テストは POX_DEBUG を明示的に切り替えるものがあるため、ここで固定のテスト用ソルトを
用意しておき、app の import が起動ガードで落ちないようにする（本番同様「ソルトあり」）。
POX_EMAIL_SALT は POX_SECRET_KEY とは独立の環境変数であることの確認も兼ねる。
"""
import os

os.environ.setdefault("POX_EMAIL_SALT", "test-insecure-email-salt")
