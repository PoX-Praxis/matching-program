@echo off
REM Qwen3-Embedding 推論サーバをローカル起動。起動後、別ターミナルでトンネルを張る:
REM   cloudflared tunnel --url http://localhost:8000
REM   または ngrok http 8000
REM 初回だけ依存インストール:  run_qwen3.bat --install
cd /d "%~dp0"

if "%QWEN3_PORT%"=="" set QWEN3_PORT=8000
if "%QWEN3_WARMUP%"=="" set QWEN3_WARMUP=1

if "%1"=="--install" (
  echo 依存をインストールします（数分・torch を含むため数百MB）...
  echo   CPU 専用で軽くしたい場合は先に:
  echo   pip install torch --index-url https://download.pytorch.org/whl/cpu
  pip install -r requirements.txt
)

echo Qwen3 サーバをポート %QWEN3_PORT% で起動します。
echo   初回はモデル（~1.2GB）を Hugging Face から自動DLします（少し待ちます）。
echo   別ターミナルで: cloudflared tunnel --url http://localhost:%QWEN3_PORT%
python server.py
