param([Parameter(Mandatory=$true)][string]$Job)
$ErrorActionPreference='Stop'
$env:PYINSTALLER_RESET_ENVIRONMENT='1'
$taskData=Get-Content -LiteralPath $Job -Raw | ConvertFrom-Json
$taskRoot=[IO.Path]::GetFullPath($taskData.app_dir)
$taskExe=Join-Path $taskRoot '3DMonitor.exe'
$taskCache=Join-Path $taskRoot 'cache\updates'
$taskInstaller=[IO.Path]::GetFullPath($taskData.installer)
$taskResult=Join-Path $taskCache 'install-result.json'
try {
 if([IO.Path]::GetDirectoryName($taskInstaller) -ne $taskCache -or [IO.Path]::GetFileName($taskInstaller) -ne "3DMonitor-Setup-v$($taskData.version).exe" -or $taskData.version -notmatch '^\d{1,5}\.\d{1,5}(\.\d{1,5})?$'){throw 'Invalid installer path'}
 if(!(Test-Path -LiteralPath (Join-Path $taskRoot 'config.json')) -or !(Test-Path -LiteralPath $taskExe)){throw 'Application not found'}
 for($taskAttempt=0;$taskAttempt -lt 120;$taskAttempt++) {
  $taskProcesses=@(Get-Process -Name '3DMonitor','K1Monitor' -ErrorAction SilentlyContinue | Where-Object { $_.Path -and [IO.Path]::GetFullPath($_.Path) -eq $taskExe })
  if(!$taskProcesses.Count){break}; Start-Sleep -Milliseconds 500
 }
 if($taskProcesses.Count){throw 'Application did not finish graceful shutdown'}
 $taskStream=[IO.File]::OpenRead($taskInstaller)
 $taskHasher=[Security.Cryptography.SHA256]::Create()
 try{$taskDigest=[BitConverter]::ToString($taskHasher.ComputeHash($taskStream)).Replace('-','').ToLowerInvariant()}finally{$taskStream.Dispose();$taskHasher.Dispose()}
 if($taskDigest -ne $taskData.sha256){throw 'Installer checksum changed'}
 $taskArguments="/S /D=$taskRoot"
 $taskTestFlag=Join-Path $taskRoot '_installer-test.flag'
 if((Test-Path -LiteralPath $taskTestFlag) -and (Get-Content -LiteralPath $taskTestFlag -Raw).Trim() -eq '1'){$taskArguments="/S /TESTMODE /TESTDIR=`"$taskRoot`" /D=$taskRoot"}
 $taskInstall=Start-Process -FilePath $taskInstaller -ArgumentList $taskArguments -WindowStyle Hidden -PassThru -Wait
 if($taskInstall.ExitCode -ne 0){throw "Installer failed: $($taskInstall.ExitCode)"}
 @{success=$true;version=$taskData.version;finished_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $taskResult -Encoding UTF8
} catch {
 @{success=$false;error='Update installation failed; the application was restarted.';finished_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $taskResult -Encoding UTF8
} finally {
 if(Test-Path -LiteralPath $taskExe){Start-Process -FilePath $taskExe -ArgumentList "--no-browser --port $($taskData.port)" -WorkingDirectory $taskRoot -WindowStyle Hidden}
}
