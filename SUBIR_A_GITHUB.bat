@echo off
rem ============================================================
rem  SUBIR A GITHUB - solo los archivos que cambiaron
rem  Doble clic para ejecutar. Al final muestra el resultado.
rem ============================================================
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo  No se encontro Python en este equipo.
  echo  Instale Python o ejecute "py subir_github.py" desde la consola.
  echo.
  pause
  exit /b 1
)
echo.
python "%~dp0subir_github.py" %*
echo.
echo ============================================================
echo  Proceso terminado. Pulse una tecla para cerrar.
echo ============================================================
pause >nul
