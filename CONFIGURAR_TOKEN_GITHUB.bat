@echo off
chcp 65001 >nul
cd /d "%~dp0"
python configurar_token_github.py
echo.
pause
