param(
    [string]$TaskName = "BazaarQiuBot-DaySnapshotWorker",
    [string]$WorkerBat = "$PSScriptRoot\run_snapshot_worker.bat"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $WorkerBat)) {
    throw "Configured worker BAT not found: $WorkerBat. Copy run_snapshot_worker.example.bat to run_snapshot_worker.bat and set credentials first."
}

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument ('/c "' + $WorkerBat + '"') `
    -WorkingDirectory (Split-Path $WorkerBat)
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Long-running BazaarDB Day snapshot worker" `
    -Force

Write-Host "Registered scheduled task: $TaskName"
