# Sets up the Living Cyrodiil companion: a Python 3.14 virtual environment
# with its libraries, and your personal settings file.
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File companion\setup.ps1

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$venv = Join-Path $here '.venv'

Write-Host 'Checking for Python 3.14...'
& py -3.14 --version
if ($LASTEXITCODE -ne 0) { Write-Host 'Python 3.14 not found. Install it with:  py install 3.14' -ForegroundColor Red; exit 1 }

if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host 'Creating virtual environment...'
    & py -3.14 -m venv "$venv"
}

Write-Host 'Installing libraries...'
& "$venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
& "$venv\Scripts\python.exe" -m pip install -r "$here\requirements.txt" --quiet
if ($LASTEXITCODE -ne 0) { Write-Host 'Library install failed (see above).' -ForegroundColor Red; exit 1 }

$local = Join-Path $here 'settings.local.toml'
if (-not (Test-Path $local)) {
    Copy-Item (Join-Path $here 'settings.example.toml') $local
    Write-Host "Created $local"
}

Write-Host ''
Write-Host 'Setup done.' -ForegroundColor Green
Write-Host "Next: open $local in Notepad and paste your OpenRouter key into api_key."
