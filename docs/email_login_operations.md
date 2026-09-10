# メールログイン運用ガイド（指示書26）

マジックリンク認証を「実際に届く形で」運用するための設定と、**最も間違えやすい鍵の扱い**をまとめる。

---

## 1. 鍵の役割 — ここを間違えると全アカウントを失う

`email_hash`（同一性の唯一の根拠）のソルトは **`POX_EMAIL_SALT`** であり、セッション署名鍵
`POX_SECRET_KEY` とは**独立**している（指示書26 §1-3 で分離済み）。両者を混同しないこと。

| 環境変数 | 用途 | 差し替えたら | 運用 |
|---|---|---|---|
| `POX_SECRET_KEY` | Flask セッション署名 | ログインセッションが切れる。**再ログインで回復** | **漏洩が疑われたら即座に差し替えてよい** |
| `POX_EMAIL_SALT` | `email_hash` のソルト | 全 `email_hash` が変わり、**全アカウントが到達不能**（同じアドレスで入っても別人扱い） | **絶対に差し替えない。値をバックアップする** |

### なぜ分離したか
分離前は両方とも `POX_SECRET_KEY` から導出していた。指示書20 は「漏洩したら
`POX_SECRET_KEY` を迷わず差し替えてよい」としていたが、その前提では**差し替えた瞬間に
全 `email_hash` が変わり全アカウントを失う**。役割を分けることで「セッション鍵は差し替え可・
ソルトは不変」を両立させた。

### 未設定時の挙動（重要）
- `POX_EMAIL_SALT` が**本番（`POX_DEBUG` が `1` 以外）で未設定なら、アプリは起動しない**
  （`app.py` の起動時ガードが `SystemExit`）。
  既定値で起動してしまうと、後から正しい値を入れた瞬間に全アカウントを失うため、**警告では
  なく起動停止**にしている。
- 開発時（`POX_DEBUG=1`）のみ、既定ソルト `dev-insecure-email-salt-change-me` に
  フォールバックする。

### バックアップ
`POX_EMAIL_SALT` は Render ダッシュボードの環境変数にしか無い。**別の安全な場所
（パスワードマネージャ等）に必ず控える。** これを失うと復旧不能。

---

## 2. メールアドレスは平文保存しない（`email_enc` 廃止）

以前は `auth_identities` / `auth_tokens` にアドレスの可逆暗号 `email_enc` を保存していたが、
**削除した**（指示書26 §3）。理由：

- アドレスの用途はマジックリンクの送信先だけ。送信先は利用者が入力した値そのもの。
- ログイン時の照合は入力アドレスの `email_hash` で足り、保存値を復号する必要が無い。
- `auth_identities.email_enc` は書かれるだけで読まれていなかった（完全な死蔵）。

→ **持たなければ漏洩も鍵管理も発生しない。** 将来「運営から利用者へ連絡する」機能を足す
なら、そのとき AEAD（`cryptography` の Fernet 等）で設計し直す。旧 `POX_EMAIL_KEY` 構想は不要。

---

## 3. SMTP（送信）設定

### 送信手段
- **Gmail は使わない。** ドメイン認証のない差出人は迷惑メール判定されやすく、届かなければ
  ログインできない。
- **送信専用サービス**（SendGrid / Resend / Amazon SES 等）を使う。送信ログで到達確認できる。
- **コードは STARTTLS（587番）専用**（`mailer.py`）。465番の SSL 専用しか使えない構成は選べない。
  **選定前に 587/STARTTLS 対応を確認する。**

### 環境変数（すべて Render ダッシュボードで手入力・`sync: false`）
| キー | 例 | 必須 |
|---|---|---|
| `POX_SMTP_HOST` | `smtp.sendgrid.net` / `smtp.resend.com` | ✅ |
| `POX_SMTP_USER` | サービス発行のユーザー名 | ✅ |
| `POX_SMTP_PASS` | API キー / パスワード | ✅ |
| `POX_SMTP_PORT` | 既定 `587` | 任意 |
| `POX_SMTP_FROM` | `noreply@pox-praxis.com` | 任意（既定は USER） |

3点（HOST/USER/PASS）が未設定だと**開発モード**になり一通も送信されない。本番でこの状態だと
起動時に警告が出る（`app.py`）。

---

## 4. 到達率 — 独自ドメインと SPF/DKIM/DMARC

`pox-praxis.com` から送る。DNS に以下を設定すると到達率が大きく変わる（日本のキャリアメール対策にも必須）。

> 手順は送信サービスによって値が異なる。ここでは「何を・どこに置くか」を記す。実際の値は
> 各サービスのダッシュボード（SendGrid: Sender Authentication / Resend: Domains）が生成する。

### (1) SPF
- `pox-praxis.com` の TXT レコードに、送信サービスを許可する SPF を追加。
  例（SendGrid）: `v=spf1 include:sendgrid.net ~all`
  （既に他の送信元がある場合は 1 本の TXT に `include:` を並べる。SPF レコードは複数不可。）

### (2) DKIM
- 送信サービスが生成する CNAME（または TXT）レコード（例 `s1._domainkey.pox-praxis.com` →
  サービス指定先）を DNS に追加。サービス側で「Verify」する。

### (3) DMARC
- `_dmarc.pox-praxis.com` の TXT に DMARC ポリシーを置く。
  最初は監視のみ: `v=DMARC1; p=none; rua=mailto:dmarc@pox-praxis.com`
  到達と認証が安定したら `p=quarantine` → `p=reject` へ段階的に上げる。

### (4) 差出人
- `POX_SMTP_FROM=noreply@pox-praxis.com`。SPF/DKIM の設定ドメインと From ドメインを一致させる
  （不一致は DMARC で弾かれる）。

### キャリアメールについて
`docomo.ne.jp` / `au.com` / `ezweb.ne.jp` / `softbank.ne.jp` / `i.softbank.jp` 等は既定で PC メールを
拒否する設定が多い。**メールが届かない＝そのアカウントに二度と入れない**ため、ログイン画面に
「`pox-praxis.com` からの受信を許可する」旨を明示している（`templates/login.html`）。案内には
**ドメイン名を必ず具体的に書く**（キャリアの受信許可はドメイン単位のため・§4-4）。

---

## 5. 設定漏れ・障害の検知

- **起動時**：本番で `POX_EMAIL_SALT` 未設定 → 起動停止。`POX_SMTP_*` 未設定 → 警告ログ。
- **送信失敗**：`/auth/request` は列挙攻撃対策で常に `{"sent": true}` を返す（維持）。ただし
  `send_magic_link` は例外を握りつぶさずログに残す（`[mailer] ERROR: ...`）。500 は返さない。
- **ログにアドレス平文・リンクを出さない**：識別は `email_hash` 先頭8文字（`addr:xxxxxxxx`）。
  開発リンクを標準出力に出すのは `POX_DEBUG=1` のときだけ。本番の開発モードは
  `[mailer] WARNING: SMTP 未設定...` とだけ記録する（有効なログインリンクをログに残さない・§5）。

---

## 6. 既存データの移行

分離・`email_enc` 削除に伴い、既存の登録（2件程度）は
`docs/migration_v2_ledger.md §4-3` の方針どおり**作り直し**とする：

- 通常 DB の行（`profiles` / `seekers` / `auth_identities` / `auth_tokens`）は削除して再登録。
- 台帳（`ledger_events`）は追記専用（指示書18 §1-4）なので消さない。過去イベントは履歴として残る。
- 新しい `POX_EMAIL_SALT` を設定した状態で、各自がメールで再ログイン → 再登録する。
