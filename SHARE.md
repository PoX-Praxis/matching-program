# PoX をその場で一時公開する（PCでサーバーを立てて、その間だけ誰でもアクセス）

PCでサーバーを起動し、**トンネル**で一時的な公開URLを発行します。
PCを閉じる／コマンドを止めればURLも無効になります。アカウント登録は不要です（cloudflared の場合）。

---

## 前提：まずローカルで動かす

```
python -m pip install -r requirements.txt
python app.py
```

ブラウザで http://127.0.0.1:5000 が開けばOK。`Ctrl+C` で停止。

---

## 方法A：Cloudflare Tunnel（推奨・アカウント不要）

ランダムな `https://xxxx.trycloudflare.com` が即発行されます。

### 1. cloudflared を入れる
- **Windows**: <https://github.com/cloudflare/cloudflared/releases> から `cloudflared-windows-amd64.exe` を入手し、`cloudflared.exe` にリネーム。
  - または `winget install --id Cloudflare.cloudflared`
- **Mac**: `brew install cloudflared`

### 2. 2つのターミナルで起動

ターミナル1（サーバー本体・公開モード）:
```
# Windows (PowerShell)
$env:POX_DEBUG="0"; python app.py

# Mac/Linux
POX_DEBUG=0 python app.py
```

ターミナル2（トンネル）:
```
cloudflared tunnel --url http://localhost:5000
```

→ 表示される `https://xxxx.trycloudflare.com` を共有すれば、誰でもアクセスできます。
コマンドを止めるとURLは無効になります。

---

## 方法B：ngrok（無料・要サインアップ）

1. <https://ngrok.com> で無料登録 → authtoken を取得
2. ngrok を入れて一度だけ: `ngrok config add-authtoken <あなたのtoken>`
3. サーバー起動（方法Aのターミナル1と同じ）
4. 別ターミナルで: `ngrok http 5000`
   → `https://xxxx.ngrok-free.app` が発行されます。

---

## 同梱の起動スクリプト

- **Windows**: `run_public.bat` をダブルクリック（debug を切ってサーバー起動）
- **Mac/Linux**: `bash run_public.sh`

起動後、別ターミナルで上記のトンネルコマンドを実行してください。

---

## ⚠️ 公開時の注意

- このプロトタイプには**ログイン認証がありません**。URLを知っている人は誰でも閲覧・登録・DM・コミュニティ作成ができます。本当のオープン公開ではなく、**信頼できる相手に短時間URLを渡す**用途に。
- `POX_DEBUG=1`（Flaskデバッガ）のまま公開しない。遠隔コード実行の穴になります。スクリプトと既定値は `0`（オフ）です。
- `pox.db` に登録データが入ります。配布したくないデータは公開前に削除してください（`pox.db` は git 管理外）。
- 添付ファイルは `static/uploads/` に保存されます。

## 環境変数まとめ

| 変数 | 既定 | 用途 |
|---|---|---|
| `POX_HOST` | `127.0.0.1` | `0.0.0.0` にすると同一LANからも直接アクセス可 |
| `POX_PORT` | `5000` | ポート変更 |
| `POX_DEBUG` | `0` | `1` で開発用デバッガ（**公開時は使わない**） |
| `POX_DB` | `pox.db` | DBファイルのパス |
| `ANTHROPIC_API_KEY` | （未設定） | 設定すると実LLM判定。未設定はデモ判定 |
