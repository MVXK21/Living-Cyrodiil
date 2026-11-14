# Starts the type-to-chat test tool.
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File companion\chat.ps1
$here = $PSScriptRoot
$py = Join-Path $here '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { Write-Host 'Run setup.ps1 first.' -ForegroundColor Red; exit 1 }
Push-Location $here
try { & $py -m living_cyrodiil.chat_cli } finally { Pop-Location }
