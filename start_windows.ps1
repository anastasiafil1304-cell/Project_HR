param(
    [switch]$ForceDocker,
    [switch]$ForceLocal,
    [switch]$ShowLauncher,
    [switch]$UseProxy,
    [string]$ProxyUrl
)

$ErrorActionPreference = 'Stop'

function Test-CommandAvailable {
    param([string]$CommandName)

    return [bool](Get-Command $CommandName -ErrorAction SilentlyContinue)
}

function Start-DockerMode {
    Write-Host 'Starting project in Docker mode...' -ForegroundColor Cyan
    docker compose up --build
}

function Get-WinHttpProxyUrl {
    if (-not (Test-CommandAvailable 'netsh')) {
        return $null
    }

    $proxyInfo = netsh winhttp show proxy | Out-String
    if ($proxyInfo -match '(?<proxy>(?:\d{1,3}\.){3}\d{1,3}:\d+|localhost:\d+)') {
        return "http://$($matches['proxy'].Trim())"
    }

    return $null
}

function Get-EffectiveProxyUrl {
    param(
        [string]$RequestedProxyUrl
    )

    if ($RequestedProxyUrl) {
        return $RequestedProxyUrl
    }

    if (Test-Path Env:HTTPS_PROXY) {
        return $env:HTTPS_PROXY
    }

    if (Test-Path Env:HTTP_PROXY) {
        return $env:HTTP_PROXY
    }

    return Get-WinHttpProxyUrl
}

function Get-PythonLauncher {
    if (Test-CommandAvailable 'py') {
        try {
            & py -3.12 -c "import sys" | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return @('py', '-3.12')
            }
        }
        catch {
        }

        return @('py', '-3')
    }

    if (Test-CommandAvailable 'python') {
        return @('python')
    }

    throw 'Python was not found. Install Python 3.10+ or use Docker mode.'
}

function Test-VenvCompatibility {
    param(
        [string]$PythonExe
    )

    if (-not (Test-Path $PythonExe)) {
        return $false
    }

    $versionOutput = & $PythonExe -c "import sys, struct; print(f'{sys.version_info.major}.{sys.version_info.minor}'); print(struct.calcsize('P') * 8)"
    if ($LASTEXITCODE -ne 0 -or $versionOutput.Count -lt 2) {
        return $false
    }

    $versionText = $versionOutput[0].Trim()
    $bits = [int]$versionOutput[1].Trim()
    $version = [version]$versionText

    return ($version -ge [version]'3.10' -and $bits -eq 64)
}

function New-CompatibleVenv {
    param(
        [string[]]$PythonLauncher,
        [string]$VenvPath
    )

    if (Test-Path $VenvPath) {
        Write-Host 'Existing .venv is incompatible. Recreating it with a supported Python...' -ForegroundColor Yellow
        Remove-Item $VenvPath -Recurse -Force
    }

    Write-Host 'Creating virtual environment .venv with supported Python...' -ForegroundColor Yellow
    Invoke-PythonLauncher -Launcher $PythonLauncher -Arguments @('-m', 'venv', '.venv')
}

function Invoke-PythonLauncher {
    param(
        [string[]]$Launcher,
        [string[]]$Arguments
    )

    if ($Launcher.Length -gt 1) {
        & $Launcher[0] $Launcher[1..($Launcher.Length - 1)] $Arguments
    } else {
        & $Launcher[0] $Arguments
    }
}

function Test-RequirementsSatisfied {
    param(
        [string]$PythonExe
    )

    $missing = @()
    $requirements = Get-Content requirements.txt

    foreach ($rawLine in $requirements) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#')) {
            continue
        }

        if ($line -notmatch '^(?<name>[^=]+)==(?<version>.+)$') {
            continue
        }

        $name = $matches['name'].Trim()
        $expectedVersion = $matches['version'].Trim()
        $packageLookupName = ($name -replace '\[.*\]$', '')
        try {
            $showOutput = & $PythonExe -m pip show $packageLookupName 2>$null
            $pipShowExitCode = $LASTEXITCODE
        }
        catch {
            $showOutput = $null
            $pipShowExitCode = 1
        }

        if ($pipShowExitCode -ne 0 -or -not $showOutput) {
            $missing += "${name}==${expectedVersion} (missing)"
            continue
        }

        $versionLine = $showOutput | Where-Object { $_ -like 'Version:*' } | Select-Object -First 1
        if (-not $versionLine) {
            $missing += "${name}==${expectedVersion} (missing version info)"
            continue
        }

        $installedVersion = $versionLine.Substring(8).Trim()
        if ($installedVersion -ne $expectedVersion) {
            $missing += "${name}==${expectedVersion} (installed: ${installedVersion})"
        }
    }

    if ($missing.Count -gt 0) {
        Write-Host 'requirements-missing' -ForegroundColor Yellow
        $missing | ForEach-Object { Write-Host $_ -ForegroundColor Yellow }
        return $false
    }

    Write-Host 'requirements-ok' -ForegroundColor DarkGreen
    return $true
}

function Clear-ProxyVariables {
    foreach ($proxyName in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'PIP_PROXY')) {
        if (Test-Path "Env:$proxyName") {
            Remove-Item "Env:$proxyName" -ErrorAction SilentlyContinue
        }
    }
}

function Enable-ProxyVariables {
    param(
        [string]$EffectiveProxyUrl
    )

    $env:HTTP_PROXY = $EffectiveProxyUrl
    $env:HTTPS_PROXY = $EffectiveProxyUrl
    $env:ALL_PROXY = $EffectiveProxyUrl
    $env:PIP_PROXY = $EffectiveProxyUrl
}

function Test-BrokenWinHttpProxy {
    param(
        [string]$AllowedProxyUrl
    )

    if (-not (Test-CommandAvailable 'netsh')) {
        return $false
    }

    $proxyInfo = netsh winhttp show proxy | Out-String
    $configuredProxy = Get-WinHttpProxyUrl

    if ($AllowedProxyUrl -and $configuredProxy -and $configuredProxy -eq $AllowedProxyUrl) {
        return $false
    }

    if ($proxyInfo -match '127\.0\.0\.1:\d+' -or $proxyInfo -match 'localhost:\d+') {
        Write-Host 'Detected local WinHTTP proxy configuration that will break pip downloads:' -ForegroundColor Red
        Write-Host $proxyInfo -ForegroundColor Red
        Write-Host 'Disable the broken Windows proxy or run: netsh winhttp reset proxy' -ForegroundColor Yellow
        Write-Host 'If you really use this proxy, run start-local-proxy.bat instead.' -ForegroundColor Yellow
        return $true
    }

    return $false
}

function Start-LocalMode {
    Write-Host 'Starting project in local Python mode...' -ForegroundColor Cyan
    $venvPython = Ensure-LocalRuntime
    Write-Host 'If PostgreSQL is unavailable, the app will use SQLite fallback automatically.' -ForegroundColor DarkYellow
    Write-Host 'Ollama is optional. Fallback mode will be used when it is unavailable.' -ForegroundColor DarkYellow
    Start-ProjectOllamaIfNeeded
    Write-Host 'Starting Flask application...' -ForegroundColor Green

    & $venvPython app.py
}

function Start-ProjectOllamaIfNeeded {
    if ((Test-Path Env:OLLAMA_ENABLED) -and $env:OLLAMA_ENABLED -in @('0', 'false', 'False', 'FALSE', 'no', 'off')) {
        return
    }

    $modelsPath = Join-Path $PSScriptRoot '.ollama\models'
    if (Test-Path -LiteralPath $modelsPath) {
        $env:OLLAMA_MODELS = (Resolve-Path -LiteralPath $modelsPath).Path
    }

    if (-not (Test-CommandAvailable 'ollama')) {
        Write-Host 'Ollama command was not found. AI functions will use fallback mode.' -ForegroundColor Yellow
        return
    }

    $ollamaListener = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ollamaListener) {
        return
    }

    Write-Host 'Starting Ollama for local AI model...' -ForegroundColor Yellow
    $ollamaOut = Join-Path $PSScriptRoot 'ollama-start.log'
    $ollamaErr = Join-Path $PSScriptRoot 'ollama-start-error.log'
    Remove-Item -LiteralPath $ollamaOut, $ollamaErr -ErrorAction SilentlyContinue
    $ollamaCommand = (Get-Command 'ollama').Source
    Start-Process -FilePath $ollamaCommand -ArgumentList @('serve') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $ollamaOut -RedirectStandardError $ollamaErr | Out-Null

    for ($i = 0; $i -lt 20; $i++) {
        $ollamaListener = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($ollamaListener) {
            return
        }
        Start-Sleep -Milliseconds 500
    }

    Write-Host 'Ollama did not start in time. The app will continue with fallback mode.' -ForegroundColor Yellow
}

function Ensure-LocalRuntime {
    $env:PIP_DEFAULT_TIMEOUT = '60'
    $effectiveProxyUrl = $null
    $shouldUseProxy = $UseProxy

    $pythonCommand = Get-PythonLauncher
    $venvPath = Join-Path $PSScriptRoot '.venv'
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $envFile = Join-Path $PSScriptRoot '.env'
    $envExample = Join-Path $PSScriptRoot '.env.example'

    if (-not (Test-VenvCompatibility -PythonExe $venvPython)) {
        New-CompatibleVenv -PythonLauncher $pythonCommand -VenvPath $venvPath
    }

    if (-not (Test-Path $venvPython)) {
        throw 'Virtual environment python was not created successfully.'
    }

    Write-Host 'Using virtual environment Python...' -ForegroundColor Yellow

    if (Test-RequirementsSatisfied -PythonExe $venvPython) {
        Write-Host 'Dependencies are already installed. Skipping pip install.' -ForegroundColor Green
    } else {
        Write-Host 'Installing missing dependencies...' -ForegroundColor Yellow
        if (-not $shouldUseProxy) {
            $autoDetectedProxyUrl = Get-EffectiveProxyUrl -RequestedProxyUrl $null
            if ($autoDetectedProxyUrl) {
                $shouldUseProxy = $true
                $effectiveProxyUrl = $autoDetectedProxyUrl
                Write-Host "Auto-detected proxy: $effectiveProxyUrl" -ForegroundColor Yellow
            }
        }

        if ($shouldUseProxy) {
            if (-not $effectiveProxyUrl) {
                $effectiveProxyUrl = Get-EffectiveProxyUrl -RequestedProxyUrl $ProxyUrl
            }

            $effectiveProxyUrl = Get-EffectiveProxyUrl -RequestedProxyUrl $ProxyUrl
            if (-not $effectiveProxyUrl) {
                throw 'Proxy mode requested, but no proxy URL was provided or detected.'
            }

            Write-Host "Using proxy: $effectiveProxyUrl" -ForegroundColor Yellow
            Enable-ProxyVariables -EffectiveProxyUrl $effectiveProxyUrl
        } else {
            Clear-ProxyVariables
            if (Test-BrokenWinHttpProxy) {
                throw 'Broken WinHTTP proxy detected. Reset the proxy and run the script again, or use proxy mode.'
            }
        }

        if ($shouldUseProxy) {
            & $venvPython -m pip install --proxy $effectiveProxyUrl --retries 10 --timeout 60 -r requirements.txt
        } else {
            & $venvPython -m pip install --retries 10 --timeout 60 -r requirements.txt
        }

        if ($LASTEXITCODE -ne 0) {
            throw 'Dependency installation failed. Fix pip/proxy issues and run the script again.'
        }
    }

    if (-not (Test-Path $envFile) -and (Test-Path $envExample)) {
        Write-Host 'Creating .env from .env.example...' -ForegroundColor Yellow
        Copy-Item $envExample $envFile
    }

    return $venvPython
}

function Start-LauncherUi {
    Write-Host 'Starting launcher UI...' -ForegroundColor Cyan
    $venvPython = Ensure-LocalRuntime
    & $venvPython launcher.py
}

$dockerAvailable = Test-CommandAvailable 'docker'

if ($ForceDocker -and -not $dockerAvailable) {
    throw 'Docker was not found. Install Docker Desktop or use the launcher UI in local mode.'
}

if ($ForceDocker) {
    Start-DockerMode
    exit $LASTEXITCODE
}

if ($ForceLocal) {
    Start-LocalMode
    exit $LASTEXITCODE
}

if ($ShowLauncher) {
    Start-LauncherUi
    exit $LASTEXITCODE
}

Start-LauncherUi
