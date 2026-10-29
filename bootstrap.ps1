param(
    [string]$Python = "python",
    [string]$Environment = ".venv",
    [string]$Wheelhouse = ""
)
$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$environmentPath = [IO.Path]::GetFullPath((Join-Path $projectRoot $Environment))
if (-not $environmentPath.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Virtual environment must be inside this project."
}
$toolSource = Join-Path $projectRoot "nanobot\nanobot\agent\tools\registry.py"
if (-not (Test-Path -LiteralPath $toolSource)) { throw "Vendored nanobot tool source is missing." }
& $Python -m venv $environmentPath
if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
$runtime = Join-Path $environmentPath "Scripts\python.exe"
$requirementsPath = Join-Path $projectRoot "requirements-app.txt"
$runtimeVersion = & $runtime -c "import sys; print(sys.version_info.major, sys.version_info.minor)"
$lockPath = Join-Path $projectRoot "requirements-lock-win-py313.txt"
if ($runtimeVersion -eq "3 13" -and (Test-Path -LiteralPath $lockPath)) { $requirementsPath = $lockPath }
$installArguments = @("-m", "pip", "install", "-r", $requirementsPath)
if ($Wheelhouse) {
    $installArguments += @("--no-index", "--find-links", [IO.Path]::GetFullPath($Wheelhouse))
}
& $runtime @installArguments
if ($LASTEXITCODE -ne 0) { throw "dependency installation failed" }
# Bind the checked-in framework source. Its full declared dependencies are
# installed because nanobot.agent's initializer imports the agent core.
& $runtime (Join-Path $projectRoot "install_tool_source.py") (Join-Path $projectRoot "nanobot")
if ($LASTEXITCODE -ne 0) { throw "tool source binding failed" }
& $runtime -m pip check
if ($LASTEXITCODE -ne 0) { throw "dependency validation failed" }
Write-Output "Installed runtime: $runtime"
