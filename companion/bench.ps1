# Runs the lore test bench: every sample NPC answers every question in
# data\bench_questions.toml, and the answers go to a report in bench_results\.
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File companion\bench.ps1
# Extra options pass through, e.g.  -File companion\bench.ps1 --model openai/gpt-5-mini
$here = $PSScriptRoot
$py = Join-Path $here '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { Write-Host 'Run setup.ps1 first.' -ForegroundColor Red; exit 1 }
Push-Location $here
try { & $py -m living_cyrodiil.lore_bench @args } finally { Pop-Location }
