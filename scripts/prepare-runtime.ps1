param(
    [string]$Python,
    [ValidateSet('runtime', 'build', 'all', 'guest', 'refind')][string]$Profile = 'all',
    [string]$PublicInstaller,
    [string]$DotNetSdk,
    [string]$Cache,
    [switch]$Offline,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not $Python) {
    $portablePython = Join-Path $projectRoot 'runtime/python/python.exe'
    if (Test-Path -LiteralPath $portablePython -PathType Leaf) { $Python = $portablePython }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) { throw 'Provide -Python with CPython 3.12+ or prepare the pinned portable Python runtime first.' }
        $Python = $pythonCommand.Source
    }
}
$arguments = @((Join-Path $PSScriptRoot 'bootstrap-dependencies.py'), '--profile', $Profile)
if ($PublicInstaller) { $arguments += @('--public-installer', $PublicInstaller) }
if ($DotNetSdk) { $arguments += @('--dotnet-sdk', $DotNetSdk) }
if ($Cache) { $arguments += @('--cache', $Cache) }
if ($Offline) { $arguments += '--offline' }
if ($CheckOnly) { $arguments += '--check-only' }
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Pinned dependency preparation failed; no setup executable was run.' }
