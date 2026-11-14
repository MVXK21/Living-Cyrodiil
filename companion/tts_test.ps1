# Voice test tool (Phase 3). Options: --text "...", --voice argonian/f, --preset alba
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File companion\tts_test.ps1
$here = $PSScriptRoot
$py = Join-Path $here '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { Write-Host 'Run setup.ps1 first.' -ForegroundColor Red; exit 1 }
Push-Location $here
try { & $py -m living_cyrodiil.tts_test @args } finally { Pop-Location }
