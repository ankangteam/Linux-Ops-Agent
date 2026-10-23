param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
$venvDir = Join-Path $PSScriptRoot '.venv'
$configPath = Join-Path $venvDir 'pyvenv.cfg'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$marker = Join-Path $venvDir '.initialized'
$utf8 = New-Object System.Text.UTF8Encoding($false)

try {
    $config = [ordered]@{}
    $original = $null
    if (Test-Path -LiteralPath $configPath) {
        $original = [IO.File]::ReadAllText($configPath)
        foreach ($line in ($original -split '\r?\n')) {
            if ($line -match '^\s*([^#=]+?)\s*=\s*(.*)$') {
                $config[$matches[1].Trim()] = $matches[2].Trim()
            }
        }
    }
    $candidates = @()
    if ($PythonPath) { $candidates += $PythonPath }
    else {
        if ($config['executable']) { $candidates += $config['executable'] }
        if ($config['home']) { $candidates += Join-Path $config['home'] 'python.exe' }
        $candidates += 'python', 'py'
    }
    $info = $null
    foreach ($candidate in $candidates) {
        try {
            $raw = & $candidate -I -c 'import sys,json,struct; print(json.dumps(dict(executable=sys._base_executable,version=sys.version.split()[0])))' 2>$null
            if ($LASTEXITCODE -ne 0) { continue }
            $found = ($raw -join "`n") | ConvertFrom-Json
            if ([version]$found.version -lt [version]'3.11') { continue }
            if ($config['version'] -and (($config['version'] -split '\.')[0..1] -join '.') -ne (($found.version -split '\.')[0..1] -join '.')) { continue }
            $info = $found
            break
        } catch { continue }
    }
    if (-not $info) { throw 'No compatible Python found. Install Python 3.11+ matching the existing venv major/minor version, or pass -PythonPath.' }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $info.executable -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create virtual environment.' }
    }
    $config['home'] = Split-Path -Parent $info.executable
    $config['executable'] = $info.executable
    $config['version'] = $info.version
    if (-not $config.Contains('include-system-site-packages')) { $config['include-system-site-packages'] = 'false' }
    $config['command'] = '"{0}" -m venv "{1}"' -f $info.executable, $venvDir
    $lines = foreach ($key in $config.Keys) { '{0} = {1}' -f $key, $config[$key] }
    [IO.File]::WriteAllText($configPath, ($lines -join "`r`n") + "`r`n", $utf8)
    & $venvPython -I -c 'import sys; assert sys.prefix != sys.base_prefix'
    if ($LASTEXITCODE -ne 0) {
        if ($null -ne $original) { [IO.File]::WriteAllText($configPath, $original, $utf8) }
        throw 'Virtual environment validation failed; original configuration restored when available.'
    }
    [IO.File]::WriteAllText($marker, $venvDir, $utf8)
    Write-Host "Virtual environment configured: $venvDir"
    exit 0
} catch {
    Write-Host "Environment initialization failed: $_" -ForegroundColor Red
    exit 1
}

