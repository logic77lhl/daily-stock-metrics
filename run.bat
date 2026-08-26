@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYW=pythonw"
where pythonw >nul 2>&1 || set "PYW=python"

set "ERRLOG=%~dp0autostart_errors.log"

echo ===== %date% %time% start ===== >> "%ERRLOG%"
"%PYW%" run_daily.py 2>> "%ERRLOG%"
echo run_daily exit=%errorlevel% >> "%ERRLOG%"
"%PYW%" run_etf_daily.py 2>> "%ERRLOG%"
echo run_etf_daily exit=%errorlevel% >> "%ERRLOG%"
"%PYW%" run_hk_daily.py 2>> "%ERRLOG%"
echo run_hk_daily exit=%errorlevel% >> "%ERRLOG%"
"%PYW%" run_buy_daily.py 2>> "%ERRLOG%"
echo run_buy_daily exit=%errorlevel% >> "%ERRLOG%"
echo ===== end ===== >> "%ERRLOG%"

endlocal
