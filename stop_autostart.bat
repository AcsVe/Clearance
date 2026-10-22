@echo off
cd /d "%~dp0"
net session >nul 2>nul || (echo [X] Run as administrator. & pause & exit /b 1)
schtasks /end /tn "CLEARANCE Website" >nul 2>nul
schtasks /delete /f /tn "CLEARANCE Website" >nul 2>nul
schtasks /delete /f /tn "CLEARANCE Hourly Backup" >nul 2>nul
schtasks /delete /f /tn "CLEARANCE Failover Watchdog" >nul 2>nul
echo Stopped and removed the CLEARANCE tasks.
pause
