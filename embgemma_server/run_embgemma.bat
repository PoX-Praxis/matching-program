@echo off
REM EmbeddingGemma-300M 推論サーバをローカル起動（ポート 8001）
REM 起動後、別ターミナルでトンネルを張る:
REM   cloudflared tunnel --url http://localhost:8001
REM 初回のみ依存インストール: run_embgemma.bat --install
cd /d "%~dp0"

if "%EMBGEMMA_PORT%"=="" set EMBGEMMA_PORT=8001
if "%EMBGEMMA_WARMUP%"=="" set EMBGEMMA_WARMUP=1

if "%1"=="--install" (
  echo 依存をインストールします...
  python -m pip install flask sentence-transformers numpy
  python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
)

echo EmbeddingGemma サーバをポート %EMBGEMMA_PORT% で起動します。
echo   初回はモデル（~600MB）を Hugging Face から自動DLします。
echo   別ターミナルで: cloudflared tunnel --url http://localhost:%EMBGEMMA_PORT%
python server.py
