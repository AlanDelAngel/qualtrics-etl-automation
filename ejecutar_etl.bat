@echo off
REM Ejecuta el dummy ETL para cada encuesta de prueba configurada en la carpeta "encuestas".
REM Programalo en el Programador de tareas de Windows (ver README).
set PYTHONUTF8=1
chcp 65001 >NUL
cd /d "%~dp0"

REM Ruta al Python que tiene instaladas las dependencias (usa "where python" para encontrarla)
set PYTHON=python

for %%F in (encuestas\*.json) do (
    echo Ejecutando %%F ...
    "%PYTHON%" etl_qualtrics.py --config "%%F"
    if errorlevel 1 echo [ERROR] Fallo %%F, revisa logs\etl.log
)

echo Proceso terminado.
