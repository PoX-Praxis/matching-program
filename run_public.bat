@echo off
REM PoX を公開モードで起動（debugger オフ）。起動後、別ターミナルでトンネルを張る。
REM   cloudflared tunnel --url http://localhost:5000
REM   または ngrok http 5000
cd /d "%~dp0"
set POX_DEBUG=0
if "%POX_PORT%"=="" set POX_PORT=5000
echo PoX をポート %POX_PORT% で起動します（debug オフ）。
echo 別ターミナルで: cloudflared tunnel --url http://localhost:%POX_PORT%
python app.py
