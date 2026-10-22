param([ValidateSet('Debug','Release')][string]$Configuration = 'Debug')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$env:DOTNET_CLI_HOME = Join-Path $projectRoot 'work/dotnet-home'
$env:NUGET_PACKAGES = Join-Path $projectRoot 'cache/nuget'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_ADD_GLOBAL_TOOLS_TO_PATH = '0'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
$env:DOTNET_GENERATE_ASPNET_CERTIFICATE = 'false'
$dotnet = Join-Path $projectRoot 'runtime/dotnet/dotnet.exe'
$dependencyLock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-dependencies.lock.json') -Raw | ConvertFrom-Json
Push-Location $projectRoot
try {
    $sdkVersion = (& $dotnet --version | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $sdkVersion -ne $dependencyLock.versions.dotnet_sdk) { throw "The locked .NET SDK $($dependencyLock.versions.dotnet_sdk) is required; run prepare-runtime.ps1 -Profile build." }
    & $dotnet build (Join-Path $projectRoot 'app/OxpInstaller.csproj') --configuration $Configuration --nologo -v:minimal "-p:RestoreConfigFile=$(Join-Path $projectRoot 'app/NuGet.Config')"
    if ($LASTEXITCODE -ne 0) { throw 'Windows app build failed.' }
}
finally { Pop-Location }
