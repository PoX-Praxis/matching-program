@echo off
REM EmbeddingGemma-300M inference server (port 8001)
REM After starting, open a tunnel in another terminal:
REM   cloudflared tunnel --url http://localhost:8001
REM First-time install: run_embgemma.bat --install
cd /d "%~dp0"

if "%EMBGEMMA_PORT%"=="" set EMBGEMMA_PORT=8001
if "%EMBGEMMA_WARMUP%"=="" set EMBGEMMA_WARMUP=1

if "%1"=="--install" (
  echo Installing dependencies...
  python -m pip install flask sentence-transformers numpy
  python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
)

echo Starting EmbeddingGemma server on port %EMBGEMMA_PORT%
echo First run will download the model (~600MB) from Hugging Face.
echo In another terminal run: cloudflared tunnel --url http://localhost:%EMBGEMMA_PORT%
python server.py
