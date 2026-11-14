# Starts the Living Cyrodiil companion. Leave the window open while you play.
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File companion\companion.ps1
$here = $PSScriptRoot
$py = Join-Path $here '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { Write-Host 'Run setup.ps1 first.' -ForegroundColor Red; exit 1 }
$host.UI.RawUI.WindowTitle = 'Living Cyrodiil companion'
Push-Location $here
try { & $py -m living_cyrodiil.main } finally { Pop-Location }
