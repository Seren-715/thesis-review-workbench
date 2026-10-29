$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$entryPath = Join-Path $projectRoot 'run_agent_ui.py'
$indexPath = Join-Path $projectRoot 'web_frontend\dist\index.html'
$outputRoot = Join-Path $projectRoot 'data\ui_jobs'
$pidPath = Join-Path $outputRoot 'server.pid'
$address = 'http://127.0.0.1:8787'

if (-not (Test-Path -LiteralPath $pythonPath)) { throw '找不到项目Python环境。' }
if (-not (Test-Path -LiteralPath $indexPath)) { throw '前端未构建。请到web_frontend执行npm install和npm run build。' }
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

try {
    $health = Invoke-RestMethod -Uri "$address/api/health" -TimeoutSec 2
    if ($health.service -eq 'paper-evidence-workbench') {
        Start-Process $address
        Write-Host "工作台已运行：$address"
        exit 0
    }
} catch { }

$process = Start-Process -FilePath $pythonPath -ArgumentList @('run_agent_ui.py', '--port', '8787') `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $outputRoot 'server.stdout.log') `
    -RedirectStandardError (Join-Path $outputRoot 'server.stderr.log')
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding utf8
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    $process.Refresh()
    if ($process.HasExited) { throw '服务启动失败，请查看data\ui_jobs\server.stderr.log。' }
    try {
        $health = Invoke-RestMethod -Uri "$address/api/health" -TimeoutSec 1
        if ($health.service -eq 'paper-evidence-workbench') {
            Start-Process $address
            Write-Host "工作台已启动：$address"
            exit 0
        }
    } catch { }
}
Write-Host '服务仍在读取证据，请稍后打开 http://127.0.0.1:8787；启动日志位于data\ui_jobs。'
