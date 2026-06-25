@echo off
REM Nomic Embed v2 推論サーバをローカル起動（ポート 8002）
REM 起動後、別ターミナルでトンネルを張る:
REM   cloudflared tunnel --url http://localhost:8002
REM 初回のみ依存インストール: run_nomic.bat --install
cd /d "%~dp0"

if "%NOMIC_PORT%"=="" set NOMIC_PORT=8002
if "%NOMIC_WARMUP%"=="" set NOMIC_WARMUP=1

if "%1"=="--install" (
  echo 依存をインストールします...
  python -m pip install flask sentence-transformers numpy
  python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
)

echo Nomic サーバをポート %NOMIC_PORT% で起動します。
echo   初回はモデルを Hugging Face から自動DLします。
echo   起動ログに実次元が表示されます。embedding_config.py に反映してください。
echo   別ターミナルで: cloudflared tunnel --url http://localhost:%NOMIC_PORT%
python server.py
