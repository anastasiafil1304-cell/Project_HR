param(
    [string]$HostName = $env:APP_HOST,
    [string]$Port = $env:APP_PORT
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$ModelsPath = Join-Path $ProjectRoot '.ollama\models'
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$OllamaOut = Join-Path $ProjectRoot 'ollama-start.log'
$OllamaErr = Join-Path $ProjectRoot 'ollama-start-error.log'

if (-not $HostName) {
    $HostName = '127.0.0.1'
}

if (-not $Port) {
    $Port = '5000'
}

$env:APP_HOST = $HostName
$env:APP_PORT = $Port
$env:DB_ENGINE = if ($env:DB_ENGINE) { $env:DB_ENGINE } else { 'sqlite' }
$env:SQLITE_PATH = if ($env:SQLITE_PATH) { $env:SQLITE_PATH } else { 'vacmatch.db' }
$env:OLLAMA_ENABLED = if ($env:OLLAMA_ENABLED) { $env:OLLAMA_ENABLED } else { '1' }
$env:OLLAMA_MODEL = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { 'mistral' }

if (Test-Path -LiteralPath $ModelsPath) {
    $env:OLLAMA_MODELS = (Resolve-Path -LiteralPath $ModelsPath).Path
}

function Test-PortOpen {
    param([int]$LocalPort)
    return [bool](Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1)
}

if ($env:OLLAMA_ENABLED -notin @('0', 'false', 'False', 'FALSE', 'no', 'off')) {
    $ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollamaCommand -and -not (Test-PortOpen -LocalPort 11434)) {
        Remove-Item -LiteralPath $OllamaOut, $OllamaErr -ErrorAction SilentlyContinue
        Start-Process -FilePath $ollamaCommand.Source -ArgumentList @('serve') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $OllamaOut -RedirectStandardError $OllamaErr | Out-Null

        for ($i = 0; $i -lt 20; $i++) {
            if (Test-PortOpen -LocalPort 11434) {
                break
            }
            Start-Sleep -Milliseconds 500
        }
    }
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python environment not found: $PythonExe"
}

& $PythonExe app.py
