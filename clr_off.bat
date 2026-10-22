@echo off
chcp 65001 >nul
title CLEARANCE - Turn the clearance system OFF
net session >nul 2>nul || (echo [X] Right-click this file and choose "Run as administrator". & pause & exit /b 1)
REM Stops 6.iajaward.org only - the other systems on this PC keep running.
schtasks /end /tn "CLEARANCE Website" >nul 2>nul
schtasks /change /tn "CLEARANCE Website" /disable >nul 2>nul
echo.
echo   6.iajaward.org (clearance system) is OFF (also after restarting the PC).
echo   Devices keep ringing from their saved schedule.
echo   To turn it back on: clr_on.bat (as administrator)
echo.
pause
