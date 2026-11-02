@echo off
chcp 65001 >nul
title CLEARANCE - Reset a password
cd /d "%~dp0"
call scripts\find_python.bat
set PYTHONUTF8=1
echo =====================================================
echo   Clearance - set a new password for an account
echo   (the password you type is hidden and stays on this PC)
echo =====================================================
"%PY%" ops\reset_password.py
pause
