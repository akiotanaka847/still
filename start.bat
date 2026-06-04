@echo off
title StillAI - Still perfect. Every time.
chcp 65001 >nul
cls
echo ============================================
echo   StillAI  -  Generador de Bodegones
echo ============================================
echo.
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [setup] No se encontro el entorno virtual. Creandolo...
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
    if not exist "%VENV_PY%" (
        echo.
        echo ERROR: No se pudo crear el entorno virtual.
        echo Asegurate de tener Python 3 instalado ^(py -3 --version^).
        pause
        exit /b 1
    )
)

echo [1/2] Instalando dependencias...
"%VENV_PY%" -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo.
    echo ERROR: No se pudieron instalar las dependencias.
    pause
    exit /b 1
)

echo [2/2] Iniciando servidor...
echo.
echo  URL: http://localhost:8000
echo  Presiona Ctrl+C para detener
echo.
"%VENV_PY%" -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
