# subhub CN refine runner
# Purpose: run refine.py from a China-located machine, then push result to GitHub.
# All-ASCII on purpose: scheduled tasks are fragile with non-ASCII paths.
# Path is reached through junction E:\subhub -> E:\AI\07-...\subhub
$ErrorActionPreference = "Continue"

$root = "E:\subhub"
$py   = "C:\Users\yjj\AppData\Local\Programs\Python\Python312\python.exe"
$log  = Join-Path $root "refine.log"

Set-Location $root

# FlClash mixed-port. If FlClash is not running, refine.py falls back to direct.
$env:SUBHUB_PROXY = "http://127.0.0.1:7890"
$env:MIHOMO_BIN   = Join-Path $root "bin\mihomo.exe"

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"=== $stamp START ===" | Out-File -Append -Encoding utf8 $log

try {
    & $py (Join-Path $root "refine.py") --push *>> $log
    $code = $LASTEXITCODE
    "=== $stamp END exit=$code ===" | Out-File -Append -Encoding utf8 $log
} catch {
    "=== $stamp ERROR: $($_.Exception.Message) ===" | Out-File -Append -Encoding utf8 $log
}

# Keep log from growing forever: trim to last 500 lines
try {
    $lines = Get-Content $log -ErrorAction Stop
    if ($lines.Count -gt 500) {
        $lines | Select-Object -Last 500 | Set-Content -Encoding utf8 $log
    }
} catch { }
