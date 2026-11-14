# Builds and runs the plugin's standalone tests (JSON + companion connection),
# outside the game, against the real Python companion code.
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File plugin\tests\run_tests.ps1

$ErrorActionPreference = 'Stop'
$root = Resolve-Path "$PSScriptRoot\..\.."
$out  = "$root\build\tests"
New-Item -ItemType Directory -Force $out | Out-Null

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products * -property installationPath
$vcvars = "$vs\VC\Auxiliary\Build\vcvarsall.bat"

# 32-bit, same v142 compiler as the plugin
$src = "$root\plugin\tests\link_test.cpp $root\plugin\src\json.cpp $root\plugin\src\companion_link.cpp $root\plugin\src\key_names.cpp"
$env:PATH = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer;$env:PATH"   # vcvarsall needs vswhere
$cmd = "`"$vcvars`" x86 -vcvars_ver=14.29 >nul && cl /nologo /EHsc /std:c++latest /W3 /O2 /MD /DWIN32 /D_WINDOWS $src /Fo`"$out\\`" /Fe`"$out\link_test.exe`" ws2_32.lib"
cmd /c $cmd
if ($LASTEXITCODE -ne 0) { Write-Host 'Test build failed.' -ForegroundColor Red; exit 1 }

$port = 47899
$result = "$out\peer_result.txt"
Remove-Item $result -ErrorAction SilentlyContinue
$py = "$root\companion\.venv\Scripts\python.exe"
$peer = Start-Process -FilePath $py -ArgumentList "-m", "tests.link_peer", $port, "`"$result`"" `
        -WorkingDirectory "$root\companion" -PassThru -NoNewWindow
Start-Sleep -Milliseconds 1500

& "$out\link_test.exe" $port
$code = $LASTEXITCODE

Start-Sleep -Milliseconds 500
$flushed = Test-Path $result
Write-Host ''
if ($flushed) { Write-Host 'PASS  companion received game_exit before the link closed' }
else { Write-Host 'FAIL  companion never received game_exit (Stop did not flush)' -ForegroundColor Red; $code = 1 }

Stop-Process -Id $peer.Id -ErrorAction SilentlyContinue
if ($code -eq 0) { Write-Host 'Plugin tests: ALL PASSED' -ForegroundColor Green } else { Write-Host 'Plugin tests: FAILED' -ForegroundColor Red }
exit $code
