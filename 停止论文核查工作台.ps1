$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pidPath = Join-Path $projectRoot 'data\ui_jobs\server.pid'
if (-not (Test-Path -LiteralPath $pidPath)) { Write-Host '没有找到工作台进程记录。'; exit 0 }
$processId = [int]((Get-Content -LiteralPath $pidPath -Raw).Trim())
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
if ($null -eq $process) { Write-Host '工作台进程已结束。'; exit 0 }
$expectedPython = (Resolve-Path -LiteralPath (Join-Path $projectRoot '.venv\Scripts\python.exe')).Path
if ($process.ExecutablePath -ne $expectedPython -or $process.CommandLine -notmatch 'run_agent_ui\.py') {
    throw '进程记录与本项目工作台不一致，未停止其他程序。'
}
Stop-Process -Id $processId
Write-Host '工作台服务已停止。已保存的论文和检查结果仍可保留查看。'
