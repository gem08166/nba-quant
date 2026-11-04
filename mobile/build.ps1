# NBA Quant APK build script (Capacitor + Android SDK). ASCII only on purpose:
# Windows PowerShell 5.1 parses BOM-less files as ANSI and breaks on Chinese text.
# Usage:
#   .\build.ps1              # full: copy www -> sync -> assembleDebug
#   .\build.ps1 -SkipWeb     # skip www copy (repackage only)
param(
    [switch]$SkipWeb
)
$ErrorActionPreference = "Stop"

# --- environment ---
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
$sdk = "$env:LOCALAPPDATA\Android\Sdk"
if (-not (Test-Path $sdk)) { throw "Android SDK not found: $sdk" }
$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk

$jdk = Get-ChildItem "C:\Program Files\Eclipse Adoptium" -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "jdk-21*" } | Sort-Object Name -Descending | Select-Object -First 1
if ($jdk) { $env:JAVA_HOME = $jdk.FullName }
else {
    # Capacitor 7 requires Java 21; 17 is not enough for javac source release 21
    throw "JDK 21 not found. Install: winget install EclipseAdoptium.Temurin.21.JDK"
}
Write-Output "JAVA_HOME = $env:JAVA_HOME"
Write-Output "ANDROID_HOME = $env:ANDROID_HOME"

# --- 1. npm deps ---
if (-not (Test-Path "$PSScriptRoot\node_modules")) {
    Write-Output "== npm install =="
    Push-Location $PSScriptRoot
    npm install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm install failed" }
    Pop-Location
}

# --- 2. regenerate static site, copy to www ---
if (-not $SkipWeb) {
    Write-Output "== export static site =="
    Push-Location "$PSScriptRoot\.."
    & "$PSScriptRoot\..\.venv\Scripts\python.exe" -m scripts.export_static --out site
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "export_static failed" }
    Pop-Location
    Write-Output "== copy site -> www =="
    $www = "$PSScriptRoot\www"
    if (Test-Path $www) { Remove-Item $www -Recurse -Force }
    robocopy "$PSScriptRoot\..\site" $www /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed (exit $LASTEXITCODE)" }
}

# --- 3. capacitor android project ---
if (-not (Test-Path "$PSScriptRoot\android")) {
    Write-Output "== cap add android =="
    Push-Location $PSScriptRoot
    npx cap add android
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "cap add failed" }
    Pop-Location
}
# AGP refuses non-ASCII paths (F:\...\nba-quant). Keep the override in sync.
$gp = "$PSScriptRoot\android\gradle.properties"
if ((Test-Path $gp) -and -not (Select-String -Path $gp -Pattern "android.overridePathCheck" -Quiet)) {
    Add-Content -Path $gp -Value "android.overridePathCheck=true" -Encoding ascii
}
Write-Output "== cap sync =="
Push-Location $PSScriptRoot
npx cap sync android
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "cap sync failed" }
Pop-Location

# --- 4. gradle assembleDebug ---
Write-Output "== gradlew assembleDebug =="
Push-Location "$PSScriptRoot\android"
.\gradlew assembleDebug --no-daemon
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "gradle build failed" }
Pop-Location

$apk = "$PSScriptRoot\android\app\build\outputs\apk\debug\app-debug.apk"
if (Test-Path $apk) {
    Copy-Item $apk "$PSScriptRoot\nbaq-debug.apk" -Force
    Write-Output "== DONE: $PSScriptRoot\nbaq-debug.apk =="
} else {
    throw "APK not found: $apk"
}
