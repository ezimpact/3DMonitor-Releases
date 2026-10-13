$ErrorActionPreference = 'Stop'
$taskExePath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '3DMonitor.exe'))
$taskProcesses = @(Get-Process -Name 3DMonitor -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and [string]::Equals($_.Path, $taskExePath, [System.StringComparison]::OrdinalIgnoreCase)
})
foreach ($taskProcess in $taskProcesses) {
    Stop-Process -Id $taskProcess.Id -ErrorAction SilentlyContinue
    $null = $taskProcess.WaitForExit(5000)
}
Write-Output ('Stopped 3DMonitor processes: {0}' -f $taskProcesses.Count)
