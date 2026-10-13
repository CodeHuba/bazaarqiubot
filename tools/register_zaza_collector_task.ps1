$ErrorActionPreference = "Stop"
$task = "BazaarDB Zaza Profile Collector"
$bat = "D:\PJ\QiuBot-github\QiuBot\tools\run_zaza_collector.bat"
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -Command "& ''{0}''; exit $LASTEXITCODE"' -f $bat
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory "D:\PJ\QiuBot-github\QiuBot"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -Force
Start-ScheduledTask -TaskName $task
Write-Output "REGISTERED"
