@echo off
title Prueba de compatibilidad
cd /d "%~dp0"
echo Ejecutando la prueba de compatibilidad...
echo.
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 prueba_compatibilidad.py
) else (
    python prueba_compatibilidad.py
)
echo.
echo (El resultado tambien quedo guardado en prueba_compatibilidad.log)
pause
