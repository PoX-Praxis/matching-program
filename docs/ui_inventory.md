# UI 棚卸し — 調査指示書24

**作成日:** 2026-09-09
**性質:** 調査のみ（コード非変更）。事実の列挙に徹し、方針・改善案・評価は含まない。
**対象:** `templates/` 配下 全14ファイル
**根拠:** 実装（各テンプレート・`app.py` のルート定義）から直接確認。導入時期のみコメント/既知履歴からの推定を含む（§末尾に明記）。

---

## §1 画面の一覧

`app.py` のルート定義と `templates/` を突き合わせた全画面。

| ファイル | ルート | 役割 | 認証（未ログインで見えるか） | 導入時期（推定） | 行数 |
|---|---|---|---|---|---|
| `_nav.html` | （部分・全ページが include） | 共通ナビゲーション | — 表示される。`localStorage pox_my_id` があればマイページ/インボックスのリンクを埋める | 増築（不明） | 40 |
| `login.html` | `GET /login` | マジックリンク送信フォーム | 見える（フォームのみ） | 指示書17 | 72 |
| `communities.html` | `GET /communities` | コミュニティ一覧・作成 | 見える（一覧）。作成は `pox_my_id` 前提 | コミュニティ機能 | 118 |
| `inbox.html` | `GET /inbox` | 承認待ち案件・未読一覧 | **見えない。401 で `/login` へ** | メッセージ機能 | 138 |
| `privacy.html` | `GET /privacy` | プライバシーポリシー | 見える（全文） | 指示書13 | 151 |
| `conversation.html` | `GET /conversation?me=&with=` | 1対1 DM | **見えない。401 で `/login` へ** | メッセージ機能 | 161 |
| `connect.html` | `GET /connect` | 「つながる」：おすすめ照合＋登録者一覧 | 見える。`pox_my_id` が無いと登録を促す。おすすめは要 id | 指示書10 | 193 |
| `about.html` | `GET /about`（`/` はここへリダイレクト） | 「PoXとは」ランディング | 見える（全文・静的） | 指示書15 | 233 |
| `edit.html` | `GET /edit?id=` | 見せ方/中身の編集 | **本人限定。401 で `/login` へ** | v3/v4 編集 | 299 |
| `index.html` | `GET /dev`（`POX_DEBUG=1` のみ・本番404） | 旧開発コンソール | 本番では 404 | 原型（プロトタイプ） | 360 |
| `profile.html` | `GET /profile/<id>` | 公開プロフィール＋接続申請＋軌跡 | 見える（公開ビュー）。接続申請は `pox_my_id` 前提 | v3/v4 | 376 |
| `register.html` | `GET /register` | 登録（構造化プロンプト＋JSON貼付） | 見える | v4 登録 | 420 |
| `community.html` | `GET /community/<id>` | コミュニティ詳細＋宣言/実績/意志形成＋チャット | 見える（宣言/実績/進行中/メンバー）。操作はセッション本人限定 | 指示書23（意志形成UI追加） | 566 |
| `mypage.html` | `GET /mypage?id=` | マイページ（プロフィール/必要像/接続/軌跡） | 公開プロフィール部は見えるが、必要像・接続・編集は **401 で `/login` へ** | v3/v4 | 632 |

**部分テンプレート・共通レイアウト:** `_nav.html`（ナビ）**のみ**。Jinja の `layout.html`／`base.html` 等の共通レイアウト継承（`{% extends %}`）は**無い**。各ページが独立した完全な HTML 文書で、`{% include "_nav.html" %}` だけを共有する。

---

## §2 用語

### 2-1. 中心概念の表記ゆれ

| 概念 | 実際に使われている表記 | 揺れの有無 |
|---|---|---|
| 必要像 | 「必要像」（共通）、「求めている」（本人申告として別概念・profile/mypage/register）、「求めている人/スキル」（register の材料説明） | 「必要像」は統一。ただし本人申告の「求めている」と併存し、**両者の区別は register/privacy でのみ明示**、profile/mypage では並記のみ |
| 意志形成 | 「意志形成」（community.html のみ・統一）。他画面には登場しない | 単一画面のため揺れ無し |
| 意志 / 現状 | 「意志」「現状」で統一（register/mypage/profile/community/privacy） | 揺れ無し |
| 接続 | 「接続」「つながる」「つながり」「マッチ/マッチング」が混在 | **揺れあり**（下記列挙） |
| コミュニティ | 「コミュニティ」で統一。ただし内部語 **`founder`（英語）** が画面に露出 | 「コミュニティ」は統一。役割名が非統一（下記） |
| 宣言 | 「宣言」（community.html・統一） | 揺れ無し |
| 実績 | 「実績」（community.html・統一） | 揺れ無し |
| 主体 | 「あなた」（約70箇所・主）、「利用者」（privacy）、「参加者」（index/community）、「メンバー」（community）、「運営主体」（privacy） | 呼びかけは「あなた」でほぼ統一。第三者指示が「利用者/参加者/メンバー」で混在 |
| 承認 / 合意 | 接続は「承認」、意志形成は「合意」、コミュニティ参加は「承認」 | 概念ごとに使い分け（接続=承認／意志形成=合意）。ただし同一語が両方に使われる箇所は無い |

#### 「接続」まわりの表記ゆれ（ファイル:行）
- 「接続」: `about.html:201,204,212`, `profile.html:323,326,342,357`, `inbox.html:122`, `index.html:61,315,318,343`, `mypage.html:148,186,232,546,548`
- 「つながる/つながり」: `_nav.html`（「つながる」＝/connect へのリンク名）, `connect.html:6,42`, `profile.html:107,117`, `about.html:175,196,201`
- 「マッチ/マッチング」: `profile.html:109,117(「この人とつながる」),291,301`, `index.html:5,28,51,55,56,155,162,221`, `connect.html:43(照合)`, `about.html:141`
- 「照合」: `connect.html:43,79,92`, `privacy.html:38`, `index.html`（v4 説明）
→ 同一概念（相手を見つけて結ぶ）に対し、入口で「つながる」、実行で「マッチング」/「照合」、成立で「接続」が使われる。

#### 内部語（英語）の画面露出
- **`vessel`**（接続の内部ID）が利用者向けメッセージに露出: `profile.html:323,326`, `inbox.html:122,124`, `index.html:315,318`, `mypage.html:546,548`（例:「🎉 接続成立！ vessel: xxx」）
- **`founder`**（英語）が画面に露出: `community.html:42（「founder: <id>」）,439（「あなたはこのコミュニティの founder です」）,455（バッジ "founder"）`
- コミュニティ作成者の役割名が3表記: **「founder」**（community.html）/ **「創設者」**（index.html:65）/ **「作成者」**（about.html:205）。`test_public_api_scope.py` は about.html 本文に "founder" が**出ない**ことを検証しており、about だけ日本語化されている。

### 2-2. 必要像の説明文（求人票との区別）

区別が**明示されている**箇所（引用）:
- `community.html:100`:「これは求人票ではありません。「どんなスキルの人が欲しいか」ではなく、**私たちに何が足りず、それを埋める人はどういう人か**——意志と現状の差分を埋める存在を書いてください。」
- `register.html:151`:「**必要像とは**:私の「意志（どこへ向かうか）」と「現状（いま何を持ち、何に縛られるか）」の**差分から、その差分を埋める存在の記述**。」
- `register.html:154`:「私の申告（「求めている」）は**検証・補強材料**であって上限ではない。」

**「スキル/欲しい人材」に寄っている記述**（引用・そのまま）:
- `register.html:109`:「**求めている**:私が自分から述べた「欲しい人・スキル・関係性」」
- `register.html:294`:「どんな人/スキル/関係性が欲しいか（特に最初に必要な相手）」
- `register.html:318`:「"求めている": "本人申告の欲しい人・関係（無ければ \"未取得\"）"」
- `about.html:124`:「共同創業者を探している人」（利用例の列挙）
→ これらは「本人申告の"求めている"」（＝必要像とは別概念）の説明であり、register 内では必要像との区別が併記されているが、語としては「欲しい人・スキル」が現れる。

### 2-3. 一人称・敬体

- **敬体（です・ます）**: about, login, privacy, connect, mypage, community（利用者向け文）, register の地の文 は「です・ます」。
- **常体（だ・である）／一人称「私」**: `register.html` の構造化プロンプト本文は**「私」一人称・常体**（例:「私の意志と現状の差分から」）。これは AI へのプロンプト本文で、利用者向け UI 文とは文体が異なる。
- 呼びかけ: UI 文はほぼ「あなた」。privacy は「あなた」＋「利用者」併用。community は「あなた」＋「メンバー」。

---

## §3 導線

### 3-1. ナビゲーション

- **共通ナビ `_nav.html` は全14ページ（partial 自身を除く13ページ）が include。** 抜けは無い。
- ナビのリンク先: `/`(PoX), `/register`(登録), `/connect`(つながる), `/communities`(コミュニティ), `/about`(PoXとは), `/privacy`(プライバシー), `/inbox`(インボックス), `/mypage`(マイページ), `/login`(ログイン)。
- **ナビに入っていない到達先**: `/edit`, `/conversation`, `/profile/<id>`, `/community/<id>`, `/dev`。いずれも文脈リンクから到達（下記）。
- 文脈リンク（実装から）:
  - `/profile/<id>` ← connect の一覧/おすすめ、community のメンバー、mypage/inbox の接続相手、（/dev の一覧）
  - `/edit` ← mypage（「見せ方/中身を編集」）
  - `/conversation?me=&with=` ← community メンバーの「DM」、inbox、mypage/profile の接続相手
  - `/community/<id>` ← communities 一覧
- **どこからも到達できない画面**: `/dev`（`POX_DEBUG=1` 時のみ・意図的に非リンク）。本番導線上で孤立している画面は無い。

### 3-2. 未ログイン時

- **表示される（公開）**: about, privacy, login, register, connect（一覧）, communities（一覧）, profile（公開ビュー）, community（宣言/実績/メンバー）。
- **401 → `/login` へ誘導**: inbox（`inbox.html:56`）, conversation（`conversation.html:66`）, edit（`edit.html:153`）, mypage（必要像/接続/編集の各 fetch・`mypage.html:250` の `handleAuth`）, community の**操作**（提起/合意/完了/取消/参加/参加申請・`community.html:151` handleAuth＋各所 `if(!sessionId) location.href="/login"`）。
- **ログインを促す表示があるページ**: community（`community.html:430`「ログインすると参加・提起できます」）, connect（`connect.html:79`「まずあなたのプロフィール登録が必要です」＝登録誘導）。
- **ログイン導線が無い/不十分な箇所**:
  - `profile.html` の接続申請（`doConnect`）は未ログイン時 `alert("マイページでIDを設定してから接続してください")`（`profile.html:285,331`）で **`/login` へは誘導しない**。
  - `_nav.html` は常に「ログイン」リンクを出すが、ログイン状態（session）を反映しない（`localStorage pox_my_id` のみ参照）。

### 3-3. 主要な動線が通っているか

| 動線 | 画面リンクだけで完結するか | 備考 |
|---|---|---|
| 初訪問 → 登録 → 照合 → 接続の申請 | **概ね通る** | `/about`→ナビ「登録」→ register 完了画面に「つながる →」リンク（`register.html:412`）→ connect の一覧/おすすめ → profile → 「この人とつながる」。ただし接続申請は `localStorage pox_my_id` 依存（下記 §9-4 の 401 問題あり） |
| ログイン → マイページ → 自分の必要像を見る | **通る** | login→（メールリンク）→`/mypage?id=<subject_id>` 着地（`app.py` auth_verify）。必要像は本人セクションに表示 |
| コミュニティ一覧 → 詳細 → 参加申請 | **通る** | communities → community → 「参加を申請する」 |
| コミュニティ詳細 → 意志形成の提起 → 合意 → 完了 | **通る（同一画面内）** | community.html 内の提起フォーム・合意/完了ボタンで完結 |
| 第三者がコミュニティの宣言と実績を読む | **通る** | community.html は未ログインで宣言/実績/進行中を表示 |

**URL 直接入力が要る箇所**:
- `/edit` はナビに無く、mypage 経由でのみ到達。mypage を開く前提として `?id=` が URL に必要（ナビの「マイページ」は `localStorage pox_my_id` がある時だけリンクが埋まる）。
- `/conversation` はナビに無く、相手を選ぶ DM リンクからのみ。
- ログイン直後の着地は `/mypage?id=<subject_id>` だが、**その後ナビの「マイページ」を押すには `localStorage pox_my_id` が要る**（session と localStorage が別系統・§9-4）。

---

## §4 見た目

### 4-1. スタイルの実装方法

- **全ページ、`<head>` 内 `<style>` ブロックにページ独自 CSS を直書き。** 外部スタイルシート（`<link rel="stylesheet">`）は**ゼロ**。共通スタイルシートは**存在しない**。
- 一部で**インライン `style=""`** も多用（特に community.html・mypage.html・_nav.html はインライン中心）。
- **CSS フレームワークは不使用**（Bootstrap/Tailwind 等の読み込み無し）。JS ライブラリ読み込みも無し（バニラ JS）。
- `_nav.html` はインラインスタイル＋末尾 `<script>` で構成。

### 4-2. 揃っていないもの（画面ごとの差異）

| 項目 | 状態 |
|---|---|
| 配色（背景） | 本文背景は白（body 明示なし）。ナビ帯は `#1a3a5c`（濃紺・全ページ共通・_nav 由来） |
| 文字色 | 多くが `color:#222`（body）。about/login/mypage は独自（`#1a3a5c` 系見出し等） |
| リンク色 | ナビ内 `#cde`。本文リンクは画面ごとに `#1a56bb`/`#555`/`#1a3a5c`/`#666` 等 **不揃い** |
| ボタン | community は `.btn/.btn-primary/.btn-sm`（`#1a3a5c`）＋赤系 `#933`/`#357`。index は `.sm`。mypage は `.mp-btn`。inbox/profile は独自 class。**クラス名も色も画面ごとに独自定義** |
| フォント | 2系統: (a) `font-family: sans-serif`（communities, community, connect, conversation, edit, inbox, index, privacy, profile, register の10画面）／(b) Hiragino/Noto スタック（about, login, mypage の3画面）。**不揃い** |
| 見出し階層 | `h1` サイズが画面ごとに `1.3rem`(community)/`1.1rem`〜 と差。about は独自の `.ab-*` 見出し体系 |
| 余白（画面幅） | `body{max-width}` が画面ごとに **900 / 860 / 820 / 800 / 760px** と不揃い（communities900・connect860・conversation800・edit820・register760 等）。about/login/mypage は body max-width パターンを使わず独自レイアウト。共通は `margin:40px auto; padding:0 16px`（body max-width 系10画面のみ） |
| フォーム入力欄 | `border:1px solid #ccc; border-radius:4px` が概ね共通だが、padding/フォントサイズ/height は画面ごとにインライン指定でばらつく |

**揃っている項目**: ナビ帯の配色・位置（全ページ `#1a3a5c` の `_nav.html`）。body max-width 系10画面の `margin:40px auto; padding:0 16px` の基本レイアウト。入力欄の border 基調（`1px solid #ccc` / radius 4px）。

### 4-3. レスポンシブ

- **`viewport` meta があるページ（4つ）**: about, connect, login, mypage。
- **`viewport` meta が無いページ（9つ＋partial）**: communities, community, conversation, edit, inbox, index, privacy, profile, register（＋`_nav.html`）。→ これらはスマホで固定幅・縮小表示になりうる。
- メディアクエリ（`@media`）による幅対応は about.html 等一部を除きほぼ無し。固定 `max-width:900px` 等の中央寄せのみ。

---

## §5 状態表示

| 画面 | 読み込み中 | 空 | エラー | 401 | 処理中の操作 |
|---|---|---|---|---|---|
| mypage | 「読み込み中...」`#loading`／軌跡「読み込み中…」 | 接続0=「まだ接続はありません。〈つながる〉から…」／軌跡「まだありません」 | プロフィール無=赤字「見つかりません」 | **`/login` へ**（handleAuth） | 保存「保存中…」／承認は成否表示（処理中表示なし） |
| community | 「読み込み中...」 | 実績「まだ完了した意志形成はありません」／進行中「進行中の…ありません」／メンバー「メンバーなし」／チャット「まだメッセージがありません」 | 「コミュニティが見つかりません」／操作失敗 `alert` | 操作は **`/login` へ** | 提起「提起中…」／他操作は完了後に再読込（処理中表示なし・ボタン非活性化なし） |
| connect | 「読み込み中…」／「照合中…（初回はベクトル化で時間）」 | 「まだおすすめできる相手がいません」／「まだ他の登録者がいません」／準備未完=注意書き | 「照合に接続できませんでした。下の一覧から…」 | 未対応（公開ページ・本人限定 fetch 無し） | 照合中表示あり |
| profile | 「読み込み中...」／軌跡「読み込み中…」 | 軌跡「まだ軌跡はありません」 | 赤字「見つかりません」／接続「マッチングエラー: …」「接続エラー: …」 | **未対応**（`/approve` の 401 を `/login` に流さない・§9-4） | 「マッチング中...」 |
| inbox | 「読み込み中...」 | 「承認待ち案件はありません」「未読メッセージはありません」 | （個別 alert） | **init は `/login` へ**。ただし doApprove の 401 は未対応（§9-4） | 承認後 alert（処理中表示なし） |
| edit | 「読み込み中...」 | 「注目ポイントはまだありません」「軌跡はまだありません」 | 赤字「見つかりません」 | **`/login` へ** | 「保存中...」 |
| conversation | （即描画） | （メッセージ0時の明示表示は薄い） | `if(!res.ok) return`（無表示） | **`/login` へ** | 送信は楽観描画 |
| communities | （即描画） | 「まだコミュニティはありません。最初に作成しましょう！」 | （薄い） | 作成は `pox_my_id` 前提 | なし |
| register | — | — | 赤字「エラー: …」 | 非該当 | （送信の処理中表示なし） |
| login | — | — | 「リンクが無効か…」（サーバ側 400 再描画） | 非該当 | 「送信中…」 |
| about / privacy | 静的（状態表示なし） | — | — | — | — |
| index(/dev) | 「読み込み中...」「計算中...」「照合中…」 | 「まだ記録がありません」「まだ seeker が…」「v4 候補がまだいません」 | 赤字 | POX_DEBUG 専用 | 各所に中表示 |

**白紙のまま何も出ないリスク**:
- `conversation.html` はメッセージ0件時に空メッセージの明示表示が弱く、`!res.ok` 時は `return` のみで何も描かない（枠だけ）。
- 空表示は多くの画面で実装済みだが、**文言・体裁は画面ごとにばらばら**（「なし」「まだ〜ありません」「〜はありません」等）。

**「処理中の操作」（ボタン押下〜完了）**: 明示的に処理中表示・ボタン非活性化を行うのは login（送信中）・register 系フォーム保存（保存中…）・community 提起（提起中…）程度。接続の承認/合意/完了/参加の各ボタンは**押下後の処理中表示・二重押下防止が概ね無い**（完了後に結果表示 or 再読込）。

---

## §6 生成に時間がかかる処理

- **登録直後の v4 ベクトル化**（`POST /seekers` が `v4_generation_status:"preparing"` を返し、バックグラウンドでベクトル化）。
  - `register.html` は完了 ID とリンクを即表示するのみで、**ベクトル化の進捗・完了は登録画面に出ない**（`/v4/seekers/<id>/status` を register は叩かない）。
  - `connect.html:149` が `/v4/seekers/<id>/status` を叩き、`preparing` のとき「あなたの照合準備がまだ完了していません」を表示（非ブロッキング）。
  - `mypage.html:465` が同 status を叩き、必要像セクションで生成状態を反映。
- **照合の実行**（connect のおすすめ・profile のマッチング）: connect は「照合中…（初回はプロフィールのベクトル化で時間がかかります）」、profile は「マッチング中...」を表示。
- **失敗時の再試行**: エンドポイント `/v4/seekers/<id>/retry` は存在するが、**どのテンプレートからも呼ばれていない**（`retry` 参照ゼロ）。UI からの再試行手段は無い。失敗状態は connect/mypage で status 由来の注意書きが出るのみ。

---

## 補足: 導入時期の根拠と限界

`app.py`/テンプレート内のコメントの指示書番号・既知の実装履歴から推定した。PR 単位まで確度をもって追えないものは「増築（不明）」とした（index.html＝原型プロトタイプ、_nav.html／inbox.html／conversation.html／communities.html／community.html の初出 PR は本調査の範囲では特定していない）。community.html の**意志形成UI部分**が指示書23 で入ったことはコミット履歴から確実。
