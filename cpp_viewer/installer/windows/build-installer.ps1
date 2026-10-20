[CmdletBinding()]
param(
    [string]$BuildDirectory = "",
    [string]$QtBinDirectory = "C:\Qt\6.11.0\mingw_64\bin",
    [string]$CMakePath = "C:\Qt\Tools\CMake_64\bin\cmake.exe",
    [string]$InnoCompilerPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$installerRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ([string]::IsNullOrWhiteSpace($BuildDirectory)) {
    $BuildDirectory = Join-Path $projectRoot "build"
}
$BuildDirectory = [IO.Path]::GetFullPath($BuildDirectory)

$windeployqt = Join-Path $QtBinDirectory "windeployqt.exe"
$application = Join-Path $BuildDirectory "qpv.exe"
$definition = Join-Path $PSScriptRoot "QuiltPatternViewer.iss"
$stageDirectory = Join-Path $installerRoot "staging"
$outputDirectory = Join-Path $installerRoot "output"

foreach ($required in @($CMakePath, $windeployqt, $InnoCompilerPath, $definition)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required file was not found: $required"
    }
}

# CMakeLists.txt is the single source of the version number.
$cmakeLists = Get-Content -LiteralPath (Join-Path $projectRoot "CMakeLists.txt") -Raw
$versionMatch = [regex]::Match($cmakeLists, 'project\s*\(\s*\S+\s+VERSION\s+([0-9]+(\.[0-9]+)*)')
if (-not $versionMatch.Success) {
    throw "Could not find the project version in CMakeLists.txt."
}
$version = $versionMatch.Groups[1].Value

if (-not $SkipBuild) {
    & $CMakePath --build $BuildDirectory --config Release --target qpv
    if ($LASTEXITCODE -ne 0) {
        throw "The qpv release build failed with exit code $LASTEXITCODE."
    }
}

if (-not (Test-Path -LiteralPath $application -PathType Leaf)) {
    throw "The application executable was not found: $application"
}

# These are fixed children of cpp_viewer\installer. Validate that relationship
# before removing a previous staging/output tree.
$installerPrefix = $installerRoot.TrimEnd('\') + '\'
foreach ($directory in @($stageDirectory, $outputDirectory)) {
    $fullDirectory = [IO.Path]::GetFullPath($directory)
    if (-not $fullDirectory.StartsWith($installerPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a directory outside the installer tree: $fullDirectory"
    }
}

if (Test-Path -LiteralPath $stageDirectory) {
    Remove-Item -LiteralPath $stageDirectory -Recurse -Force
}
if (Test-Path -LiteralPath $outputDirectory) {
    Remove-Item -LiteralPath $outputDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $stageDirectory, $outputDirectory | Out-Null

Copy-Item -LiteralPath $application -Destination (Join-Path $stageDirectory "qpv.exe")

& $windeployqt --release --compiler-runtime --no-translations `
    --dir $stageDirectory (Join-Path $stageDirectory "qpv.exe")
if ($LASTEXITCODE -ne 0) {
    throw "windeployqt failed with exit code $LASTEXITCODE."
}

& $InnoCompilerPath `
    "/DMyAppVersion=$version" `
    "/DStageDir=$stageDirectory" `
    "/DOutputDir=$outputDirectory" `
    $definition
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE."
}

$installer = Get-ChildItem -LiteralPath $outputDirectory -Filter "*-setup.exe" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($null -eq $installer) {
    throw "Inno Setup completed without producing an installer."
}

Write-Output $installer.FullName
