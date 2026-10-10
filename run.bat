@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYW=pythonw"
where pythonw >nul 2>&1 || set "PYW=python"

set "ERRLOG=%~dp0autostart_errors.log"

echo ===== %date% %time% start ===== >> "%ERRLOG%"
rem 唯一入口：与云端 daily.yml 跑的是同一段编排（run_all.py）。
rem 以前这里是 4 行内联的步骤序列（run_daily / run_etf_daily / run_hk_daily /
rem run_buy_daily），既不含回测/价值标的/站点，也和云端那 11 步不是同一套 ——
rem 于是「修好一处」只到得了其中一条路径。
"%PYW%" run_all.py --mode full 2>> "%ERRLOG%"
echo run_all exit=%errorlevel% >> "%ERRLOG%"
echo ===== end ===== >> "%ERRLOG%"

endlocal
