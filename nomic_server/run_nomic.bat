@echo off
REM Nomic Embed v2 inference server (port 8002)
REM After starting, open a tunnel in another terminal:
REM   cloudflared tunnel --url http://localhost:8002
REM First-time install: run_nomic.bat --install
cd /d "%~dp0"

if "%NOMIC_PORT%"=="" set NOMIC_PORT=8002
if "%NOMIC_WARMUP%"=="" set NOMIC_WARMUP=1

if "%1"=="--install" (
  echo Installing dependencies...
  python -m pip install flask sentence-transformers numpy
  python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
)

echo Starting Nomic server on port %NOMIC_PORT%
echo First run will download the model from Hugging Face.
echo Check the startup log for the actual embedding dimension (fills TODO in embedding_config.py).
echo In another terminal run: cloudflared tunnel --url http://localhost:%NOMIC_PORT%
python server.py
