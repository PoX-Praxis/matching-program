# 判断の一覧（docs/decisions/）

判断ごとに 1 ファイル。決まりは `docs/OPERATIONS.md` §3、テンプレートは `_template.md`。

状態は **確定／下書き（要確認）／置き換え済み** のどれか。

| 番号 | 決めたこと | 状態 |
|---|---|---|
| [001](001-structuring-by-users-ai.md) | 構造化は利用者の AI にさせ、サーバーは判断しない | 下書き（要確認） |
| [002](002-untrusted-input.md) | 受け取った値は無検証で信頼しない | 下書き（要確認） |
| [003](003-private-public-layers.md) | 本人の層と公開の層を分ける | 下書き（要確認） |
| [004](004-pii-mask-before-embedding.md) | 埋め込みの前に PII をマスクする | 下書き（要確認） |
| [005](005-asymmetric-search.md) | 非対称の検索（必要像＝query、相手の文＝passage） | 下書き（要確認） |
| [006](006-single-model-tag-isolation.md) | 埋め込みのモデルは 1 つにし、タグで隔離する | 下書き（要確認） |
| [007](007-no-numbers-neutral-order.md) | 数値を画面と公開 API に出さず、並びを中立にする | 下書き（要確認） |
| [008](008-quote-not-generate-reasons.md) | 照合の理由は生成せず、文の対の引用で示す | 下書き（要確認） |
| [009](009-upper-conjunction-lower-closed.md) | 照合は 2 層にし、上層は連言、下層は閉じる | 下書き（要確認） |
| [010](010-provisional-thresholds-preregistration.md) | 閾値は暫定とし、事前登録した規則で決める | 下書き（要確認） |
| [011](011-append-only-ledger-anchor.md) | 追記専用の台帳と、日次のアンカー | 下書き（要確認） |
| [012](012-ledger-first-external-later.md) | 台帳を先に、外部依存を後に | 下書き（要確認） |
| [013](013-email-keyed-hash.md) | メールアドレスは鍵付きハッシュで持つ | 下書き（要確認） |
| [014](014-fail-fast-on-missing-config.md) | 安全に関わる設定が無ければ起動を止める | 下書き（要確認） |
| [015](015-no-results-on-stub.md) | 埋め込みが stub のときは照合の結果を出さない | 下書き（要確認） |
| [016](016-close-not-delete-old-paths.md) | 古い経路は消さずに閉じる | 下書き（要確認） |
