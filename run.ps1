# 启动机械选型引擎（本地服务 + 已构建前端），浏览器访问 http://127.0.0.1:8756
# 开发前端时改用两个进程：本脚本 + 另开 `cd web; npm run dev`（走 5173，自动代理 API）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path "web/dist/index.html")) {
    Write-Host "前端尚未构建，正在构建..." -ForegroundColor Yellow
    Push-Location web; npm run build; Pop-Location
}
& ".venv/Scripts/python.exe" -m server --port 8756
