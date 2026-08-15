[CmdletBinding()]
param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = $PSScriptRoot
$venvDir = Join-Path $projectRoot ".venv-build"
$buildPython = Join-Path $venvDir "Scripts\python.exe"

Push-Location $projectRoot
try {
    python -c "import os, struct, sys; assert os.name == 'nt' and struct.calcsize('P') == 8, 'A 64-bit Windows Python is required'; assert sys.version_info >= (3, 10), 'Python 3.10 or newer is required'"
    if ($LASTEXITCODE -ne 0) { throw "A 64-bit Windows Python 3.10+ installation is required." }

    if (-not (Test-Path -LiteralPath $buildPython)) {
        python -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "Could not create the build environment." }
    }

    if (-not $SkipDependencyInstall) {
        & $buildPython -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { throw "Could not update pip." }
        & $buildPython -m pip install -r requirements-build.txt
        if ($LASTEXITCODE -ne 0) { throw "Could not install build dependencies." }
    }

    & $buildPython packaging\create_icon.py
    if ($LASTEXITCODE -ne 0) { throw "Could not generate the application icon." }
    & $buildPython packaging\prepare_ffmpeg.py
    if ($LASTEXITCODE -ne 0) { throw "Could not prepare FFmpeg." }
    & $buildPython packaging\prepare_licenses.py
    if ($LASTEXITCODE -ne 0) { throw "Could not collect dependency licenses." }
    & $buildPython -m PyInstaller --noconfirm --clean MyFansDownloader.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    $exePath = Join-Path $projectRoot "dist\MyFansDownloader.exe"
    if (-not (Test-Path -LiteralPath $exePath)) {
        throw "Build completed without producing MyFansDownloader.exe."
    }
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $exePath
    $checksumPath = Join-Path $projectRoot "dist\MyFansDownloader.exe.sha256"
    $checksumLine = "$($hash.Hash.ToLowerInvariant())  MyFansDownloader.exe"
    [System.IO.File]::WriteAllText(
        $checksumPath,
        $checksumLine + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
    $licenseArchive = Join-Path $projectRoot "dist\THIRD_PARTY_LICENSES.zip"
    Compress-Archive -Path ".build-tools\licenses\*" -DestinationPath $licenseArchive -Force
    Copy-Item -LiteralPath "LICENSE" -Destination "dist\LICENSE.txt" -Force
    Copy-Item -LiteralPath "THIRD_PARTY_NOTICES.md" -Destination "dist\THIRD_PARTY_NOTICES.md" -Force
    Write-Host "Built: $exePath"
    Write-Host "SHA256: $($hash.Hash)"
    Write-Host "Checksum: $checksumPath"
    Write-Host "Licenses: $licenseArchive"
}
finally {
    Pop-Location
}
