param(
    [switch]$Stop,
    [switch]$NoBrowser,
    [ValidateRange(1024, 65535)][int]$Port = 8501,
    [string]$PythonPath
)

# This script is shipped with the project; local_data contains runtime logs only.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $projectRoot 'app.py'
if (-not $PythonPath) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
$serviceUrl = "http://127.0.0.1:$Port"
$logRoot = Join-Path $projectRoot 'local_data'
$mutex = [System.Threading.Mutex]::new($false, "Local\XhsSellerCopilot-$Port")
$locked = $false
$launchedProcess = $null

function Get-AppListener {
    # Enumerate successfully rather than treating a failed port query as an empty port.
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object { $_.LocalPort -eq $Port })
    if ($listeners.Count -eq 0) { return $null }
    $processIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($processIds.Count -ne 1 -or
        @($listeners | Where-Object { $_.LocalAddress -ne '127.0.0.1' }).Count -gt 0) {
        throw "Port $Port belongs to another application or non-loopback listener. No process was stopped."
    }
    $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($processIds[0])"
    $escaped = [regex]::Escape($appPath)
    $pathPattern = '"' + $escaped + '"'
    if ($appPath -notmatch '\s') { $pathPattern = '(?:' + $pathPattern + '|' + $escaped + ')' }
    $commandPattern = '(?:^|\s)-m\s+streamlit\s+run\s+' + $pathPattern + '(?=\s|$)'
    if (-not $serviceProcess -or -not $serviceProcess.CommandLine -or
        $serviceProcess.CommandLine -notmatch $commandPattern -or
        $serviceProcess.CommandLine -notmatch "(?:^|\s)--server.port\s+$Port(?=\s|$)" -or
        $serviceProcess.CommandLine -notmatch '(?:^|\s)--server.address\s+127\.0\.0\.1(?=\s|$)') {
        throw "Port $Port belongs to another application. No process was stopped."
    }
    return $serviceProcess
}

try {
    try { $locked = $mutex.WaitOne(30000) }
    catch [System.Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Another start/stop operation is still running. Try again shortly.' }
    $existingService = Get-AppListener
    if ($Stop) {
        if ($existingService) {
            # Recheck the process identity immediately before terminating it.
            $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($existingService.ProcessId)"
            if ($current) {
                if ($current.CreationDate -ne $existingService.CreationDate -or
                    $current.CommandLine -ne $existingService.CommandLine) {
                    throw 'The listener changed during inspection. No process was stopped; inspect again.'
                }
                Stop-Process -Id $current.ProcessId -ErrorAction Stop
            }
            $timer = [System.Diagnostics.Stopwatch]::StartNew()
            while ((Get-AppListener) -and $timer.Elapsed.TotalSeconds -lt 5) {
                Start-Sleep -Milliseconds 100
            }
            if (Get-AppListener) { throw 'The service has not stopped. Try again shortly.' }
            Write-Output 'Seller Copilot stopped.'
        } else { Write-Output 'Seller Copilot is already stopped.' }
        exit 0
    }
    if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
        throw 'The application file app.py is missing. Copy the complete project first.'
    }
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw 'Python is missing. Create .venv and install requirements-lock.txt first, or pass -PythonPath.'
    }
    if (-not $existingService) {
        New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
        # Python closes inherited pipe handles explicitly, so calling from a terminal,
        # test runner or another process returns without waiting for the server to exit.
        $launcherPath = Join-Path $PSScriptRoot 'start-server.py'
        $spawnedId = & $PythonPath $launcherPath --app $appPath --port $Port
        if ($LASTEXITCODE -ne 0) { throw 'Could not launch the service. Check Python and dependencies.' }
        $launchedProcess = Get-Process -Id ([int]$spawnedId) -ErrorAction Stop
    }
    $ready = $false
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt 20) {
        if ($launchedProcess -and $launchedProcess.HasExited) {
            throw "The service exited. Read local_data/service-$Port.stderr.log and check dependencies."
        }
        $owned = Get-AppListener
        if ($owned) {
            try {
                $response = Invoke-WebRequest -UseBasicParsing -Uri "$serviceUrl/_stcore/health" -TimeoutSec 2
                if ($response.StatusCode -eq 200 -and $response.Content.Trim() -eq 'ok') {
                    $ready = $true
                    break
                }
            } catch [System.Net.WebException] { }
        }
        Start-Sleep -Milliseconds 200
    }
    if (-not $ready) { throw "The service did not become ready. Read local_data/service-$Port.stderr.log." }
    Write-Output "Seller Copilot is ready: $serviceUrl (PID: $($owned.ProcessId) )"
    if (-not $NoBrowser) { Start-Process $serviceUrl }
} catch {
    # An unsuccessful launch must not leave a hidden server started by this invocation.
    if ($launchedProcess -and -not $launchedProcess.HasExited) {
        # A virtual-environment launcher can own a separate Python listener process.
        try {
            $owned = Get-AppListener
            if ($owned) { Stop-Process -Id $owned.ProcessId -ErrorAction SilentlyContinue }
        } catch { }
        Stop-Process -Id $launchedProcess.Id -ErrorAction SilentlyContinue
    }
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
