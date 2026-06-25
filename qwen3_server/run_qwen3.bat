@echo off
REM Qwen3-Embedding inference server (port 8000)
REM After starting, open a tunnel in another terminal:
REM   cloudflared tunnel --url http://localhost:8000
REM First-time install: run_qwen3.bat --install
cd /d "%~dp0"

if "%QWEN3_PORT%"=="" set QWEN3_PORT=8000
if "%QWEN3_WARMUP%"=="" set QWEN3_WARMUP=1

if "%1"=="--install" (
  echo Installing dependencies...
  python -m pip install flask sentence-transformers numpy
  python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
)

echo Starting Qwen3 server on port %QWEN3_PORT%
echo First run will download the model (~1.2GB) from Hugging Face.
echo In another terminal run: cloudflared tunnel --url http://localhost:%QWEN3_PORT%
python server.py
