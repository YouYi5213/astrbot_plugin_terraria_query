# 安装 commit-msg hook，自动移除 Cursor 自动追加的 Co-authored-by
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$hookDir = Join-Path $root ".git\hooks"
$src = Join-Path $PSScriptRoot "commit-msg"
Copy-Item $src (Join-Path $hookDir "commit-msg") -Force
Write-Host "已安装 commit-msg hook -> $hookDir"
