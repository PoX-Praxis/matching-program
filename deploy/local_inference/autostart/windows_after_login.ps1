# PoX の llama-server とトンネルを、Windows にログインしたときに立ち上げる（指示書81 §4）。
# 入れ物は docker の --restart unless-stopped で立ち上がる。ここでは、無ければ起こし、返事を待って、PoX に知らせる。
# 登録（1 回だけ。PowerShell を管理者で開いて、このフォルダで）:
#   schtasks /Create /SC ONLOGON /TN "PoX llama" /TR "powershell -ExecutionPolicy Bypass -File \"$PWD\windows_after_login.ps1\"" /RL HIGHEST
$ErrorActionPreference = "Continue"
$Kit = Split-Path -Parent $PSScriptRoot
Set-Location $Kit
for ($i = 0; $i -lt 60; $i++) {                       # Docker Desktop が動き出すのを待つ（最大 10 分）
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 10
}
py start_server.py --if-missing
py check_server.py --health
# トンネル: cloudflared をサービスとして入れていれば（README の 8）、Windows が立ち上げる。ここでは何もしない
py nudge_pox.py
