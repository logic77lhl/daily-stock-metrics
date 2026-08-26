# Register auto-start at logon.
# Preferred: Scheduled Task (needs admin). Fallback: Startup-folder shortcut (no admin needed).
# Usage:  powershell -ExecutionPolicy Bypass -File .\setup_autostart.ps1

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$BatPath = Join-Path $ProjectDir "run.bat"
$TaskName = "WeeklyStockMetrics_Daily"

if (-not (Test-Path -LiteralPath $BatPath)) {
    Write-Error "run.bat not found: $BatPath"
    exit 1
}

function Register-AsScheduledTask {
    try {
        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($existing) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }

        $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BatPath`"" -WorkingDirectory $ProjectDir
        $triggerLogon = New-ScheduledTaskTrigger -AtLogOn
        $triggerDaily = New-ScheduledTaskTrigger -Daily -At "19:00"
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($triggerLogon, $triggerDaily) -Settings $settings -Principal $principal -Description "Daily A-share top100 KDJ-J and PE/PB percentile" -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Register-AsStartupShortcut {
    $startup = [Environment]::GetFolderPath("Startup")
    $lnk = Join-Path $startup "WeeklyStockMetrics.lnk"
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($lnk)
    $sc.TargetPath = $BatPath
    $sc.WorkingDirectory = $ProjectDir
    $sc.WindowStyle = 7
    $sc.Description = "Daily A-share top100 metrics"
    $sc.Save()
    return $lnk
}

Write-Host "Trying Scheduled Task (best option)..."
if (Register-AsScheduledTask) {
    Write-Host ""
    Write-Host "OK - Registered as Scheduled Task: $TaskName"
    Write-Host "  - Runs at logon + daily catch-up at 19:00 (both skip before 17:00)"
    Write-Host ("  - Output: " + $ProjectDir + "\output\<date>\")
    Write-Host "  Test:   Start-ScheduledTask -TaskName $TaskName"
    Write-Host "  Remove: powershell -ExecutionPolicy Bypass -File .\remove_autostart.ps1"
} else {
    Write-Host "Scheduled Task needs admin rights. Falling back to Startup-folder shortcut..."
    $lnk = Register-AsStartupShortcut
    Write-Host ""
    Write-Host "OK - Created Startup shortcut (runs at next logon):"
    Write-Host ("  " + $lnk)
    Write-Host ("  - Output: " + $ProjectDir + "\output\<date>\")
    Write-Host "  Remove: powershell -ExecutionPolicy Bypass -File .\remove_autostart.ps1"
}
