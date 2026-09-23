# 一键打包 —— 前端 → Python sidecar → Tauri 安装包
#
#   ./packaging/build.ps1              # 全量
#   ./packaging/build.ps1 -SkipTauri   # 只出独立版（不需要 Rust）
#
# 产物：
#   dist/mds-server/            独立版，双击 mds-server.exe 即用（不需要 Rust）
#   desktop/src-tauri/target/release/bundle/nsis/*.exe    Windows 安装包（需要 Rust）

param(
    [switch]$SkipWeb,
    [switch]$SkipServer,
    [switch]$SkipTauri
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$Py = Join-Path $Root ".venv/Scripts/python.exe"
if (-not (Test-Path $Py)) { throw "找不到虚拟环境：$Py（先建好 .venv 并装依赖）" }

function Step($msg) { Write-Host "`n▶ $msg" -ForegroundColor Cyan }

# ── 1. 前端 ──
if (-not $SkipWeb) {
    Step "构建前端"
    Push-Location (Join-Path $Root "web")
    if (-not (Test-Path "node_modules")) { npm install }
    npm run build
    Pop-Location
}

# ── 2. Python sidecar ──
if (-not $SkipServer) {
    Step "打包服务端（PyInstaller）"
    & $Py -m PyInstaller (Join-Path $Root "packaging/mds-server.spec") `
        --noconfirm --distpath (Join-Path $Root "dist") --workpath (Join-Path $Root "build")

    $exe = Join-Path $Root "dist/mds-server/mds-server.exe"
    if (-not (Test-Path $exe)) { throw "PyInstaller 没有产出 $exe" }

    Step "冒烟测试打包版"
    & $Py (Join-Path $Root "packaging/smoke_test.py")
}

# ── 3. Tauri ──
if ($SkipTauri) {
    Write-Host "`n跳过 Tauri（-SkipTauri）。独立版已就绪：dist/mds-server/mds-server.exe" -ForegroundColor Green
    exit 0
}

# rustup 安装时用了 --no-modify-path（不动全局 PATH），所以这里自己找一下
$CargoBin = Join-Path $env:USERPROFILE ".cargo/bin"
if ((Test-Path $CargoBin) -and ($env:PATH -notlike "*$CargoBin*")) {
    $env:PATH = "$env:PATH;$CargoBin"
}

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Host "`n找不到 cargo —— Tauri 需要 Rust 工具链。" -ForegroundColor Yellow
    Write-Host "  安装：https://rustup.rs  （MSVC 构建工具本机已具备）"
    Write-Host "  装好后重新跑本脚本；或用 -SkipTauri 只出独立版。"
    Write-Host "`n独立版已就绪：dist/mds-server/mds-server.exe" -ForegroundColor Green
    exit 0
}

Step "准备 Tauri 资源"
$SidecarSrc = Join-Path $Root "dist/mds-server"
$SidecarDst = Join-Path $Root "desktop/src-tauri/mds-server"
if (Test-Path $SidecarDst) { Remove-Item $SidecarDst -Recurse -Force }
Copy-Item $SidecarSrc $SidecarDst -Recurse

Step "构建 Tauri 安装包"
Push-Location (Join-Path $Root "desktop/src-tauri")
if (-not (Get-Command cargo-tauri -ErrorAction SilentlyContinue)) {
    Write-Host "  首次构建：安装 tauri-cli（需编译，约 3 分钟）" -ForegroundColor Yellow
    cargo install tauri-cli --version "^2" --locked
}
cargo tauri build
Pop-Location

Write-Host "`n完成。安装包在 desktop/src-tauri/target/release/bundle/nsis/" -ForegroundColor Green
