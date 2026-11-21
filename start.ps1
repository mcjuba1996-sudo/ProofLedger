param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$candidates = @()
$launcher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($launcher) {
    $candidates += [pscustomobject]@{ Exe = $launcher.Source; Prefix = @('-3') }
}
foreach ($name in @('python.exe', 'python3.exe')) {
    $candidate = Get-Command $name -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.Source -notlike '*\Microsoft\WindowsApps\*') {
        $candidates += [pscustomobject]@{ Exe = $candidate.Source; Prefix = @() }
    }
}
# Optional fallback for a machine running Codex; the package also works with ordinary Python.
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $bundledPython) {
    $candidates += [pscustomobject]@{ Exe = $bundledPython; Prefix = @() }
}
$selected = $null
foreach ($candidate in $candidates) {
    $prefix = $candidate.Prefix
    try {
        & $candidate.Exe @prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { $selected = $candidate; break }
    } catch { continue }
}
if (-not $selected) {
    Write-Host 'Python 3.11+ is required. Install Python and run START.cmd again.'
    exit 1
}
$prefix = $selected.Prefix
$launchArguments = @('-m', 'proofledger.live', '--port', $Port)
if ($NoBrowser) { $launchArguments += '--no-browser' }
& $selected.Exe @prefix @launchArguments
exit $LASTEXITCODE
