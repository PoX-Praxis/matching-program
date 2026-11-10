"""リポジトリの最上位の pytest の設定。

archive/ の中のテスト（使わなくなった道具の記録。例: 66C の L の計測）は集めない（KAORU 2026-11-10）。
"""
collect_ignore_glob = ["archive/*"]
