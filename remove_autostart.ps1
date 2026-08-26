# Remove auto-start (both Scheduled Task and Startup shortcut if present).
$TaskName = "WeeklyStockMetrics_Daily"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task: $TaskName"
    } catch {
        Write-Host "Could not remove scheduled task (needs admin): $TaskName"
    }
} else {
    Write-Host "No scheduled task found."
}

$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "WeeklyStockMetrics.lnk"
if (Test-Path -LiteralPath $lnk) {
    Remove-Item -LiteralPath $lnk -Force
    Write-Host "Removed startup shortcut: $lnk"
} else {
    Write-Host "No startup shortcut found."
}
