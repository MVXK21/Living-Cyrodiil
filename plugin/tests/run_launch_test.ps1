# Tests the plugin's companion launcher against the release folder, outside the game.
# Run after tools\make_release.py, from the project folder:
#   powershell -ExecutionPolicy Bypass -File plugin\tests\run_launch_test.ps1
# The companion writes into a scratch folder (LC_USER_DIR), never a real player folder.
$ErrorActionPreference = 'Stop'
$root = Resolve-Path "$PSScriptRoot\..\.."
$out = "$root\build\tests"
New-Item -ItemType Directory -Force $out | Out-Null
$release = Get-ChildItem "$root\build\release" -Directory | Where-Object Name -like 'LivingCyrodiil-*-alpha' | Select-Object -First 1
if (-not $release) { Write-Host 'Run tools\make_release.py first.' -ForegroundColor Red; exit 1 }

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products * -property installationPath
$vcvars = "$vs\VC\Auxiliary\Build\vcvarsall.bat"
$env:PATH = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer;$env:PATH"
$src = "$root\plugin\tests\launch_test.cpp $root\plugin\src\companion_launch.cpp"
$cmd = "`"$vcvars`" x86 -vcvars_ver=14.29 >nul && cl /nologo /EHsc /std:c++latest /W3 /O2 /MD /DWIN32 /D_WINDOWS /DUNICODE /D_UNICODE $src /Fo`"$out\\`" /Fe`"$out\launch_test.exe`" ws2_32.lib shell32.lib"
cmd /c $cmd
if ($LASTEXITCODE -ne 0) { Write-Host 'Test build failed.' -ForegroundColor Red; exit 1 }

# Stand where the plugin DLL does.
$plugins = Join-Path $release.FullName 'OBSE\Plugins'
Copy-Item "$out\launch_test.exe" $plugins -Force
$scratch = "$out\launch_user"
Remove-Item $scratch -Recurse -Force -ErrorAction SilentlyContinue
$env:LC_USER_DIR = $scratch
$port = 47833      # the companion's default ([game] port in settings.toml)
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Something already listens on port $port (a companion?). Close it first." -ForegroundColor Red; exit 1 }
& "$plugins\launch_test.exe" $port
$code = $LASTEXITCODE
Remove-Item "$plugins\launch_test.exe" -Force

Start-Sleep -Seconds 2
$still = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($still) { Write-Host "FAIL  the companion still runs after the host exited" -ForegroundColor Red; $code = 1 }
else { Write-Host "PASS  the companion closed with the host" }
foreach ($f in 'settings.toml', 'logs\companion.log', 'world_knowledge.txt') {
    if (Test-Path "$scratch\$f") { Write-Host "PASS  created $f in the user folder" }
    else { Write-Host "FAIL  no $f in the user folder" -ForegroundColor Red; $code = 1 }
}
if ($code -eq 0) { Write-Host 'Launch test: ALL PASSED' -ForegroundColor Green } else { Write-Host 'Launch test: FAILED' -ForegroundColor Red }
exit $code
