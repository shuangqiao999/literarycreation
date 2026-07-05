# LiteraryCreation build script — runs all three stages and copies installer to release/
param(
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$SkipTauri
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location -LiteralPath $root

$releaseDir = Join-Path $root "release"
if (-not (Test-Path -LiteralPath $releaseDir)) {
    New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
}

try {
    # Stage 1: PyInstaller backend
    if (-not $SkipBackend) {
        Write-Host "=== Stage 1: PyInstaller backend ===" -ForegroundColor Cyan
        python -m PyInstaller literary-creation-backend.spec --noconfirm
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

        # Copy backend to Tauri resources
        $backendDir = Join-Path $root "apps\literary-creation\src-tauri\resources\literary-creation-backend"
        if (Test-Path -LiteralPath $backendDir) { Remove-Item -LiteralPath $backendDir -Recurse -Force }
        Copy-Item -LiteralPath (Join-Path $root "dist\literary-creation-backend") -Destination $backendDir -Recurse
        Write-Host "  Backend prepared -> tauri resources/" -ForegroundColor Green
    }

    # Stage 2: Frontend (Vite)
    if (-not $SkipFrontend) {
        Write-Host "=== Stage 2: Vite frontend ===" -ForegroundColor Cyan
        Push-Location -LiteralPath "$root\apps\literary-creation"
        cmd /c "npm run build"
        if ($LASTEXITCODE -ne 0) { throw "Vite build failed" }
        Pop-Location
        Write-Host "  Frontend built" -ForegroundColor Green
    }

    # Stage 3: Tauri + NSIS installer
    if (-not $SkipTauri) {
        Write-Host "=== Stage 3: Tauri + NSIS installer ===" -ForegroundColor Cyan
        Push-Location -LiteralPath "$root\apps\literary-creation"
        cmd /c "npx tauri build --bundles nsis"
        if ($LASTEXITCODE -ne 0) { throw "Tauri build failed" }
        Pop-Location

        # Copy installer to release/
        $src = Join-Path $root "apps\literary-creation\src-tauri\target\release\bundle\nsis\LiteraryCreation_*_x64-setup.exe"
        $files = Get-ChildItem -Path (Split-Path $src) -Filter "LiteraryCreation_*_x64-setup.exe" -ErrorAction SilentlyContinue
        if ($files) {
            $latest = $files | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            $dest = Join-Path $releaseDir $latest.Name
            Copy-Item -LiteralPath $latest.FullName -Destination $dest -Force
            Write-Host "  Installer -> release/$($latest.Name)" -ForegroundColor Green
        }
    }

    Write-Host "`n=== Build complete ===" -ForegroundColor Green
    Get-ChildItem -LiteralPath $releaseDir -Filter "*.exe" | ForEach-Object {
        Write-Host "  $($_.Name)  $([math]::Round($_.Length/1MB,1)) MB"
    }
}
finally {
    Pop-Location
}
