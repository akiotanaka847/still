@echo off
title PSD Smart Object Replacer
chcp 65001 >nul
cls
echo ============================================
echo   PSD Smart Object Replacer  v1.0
echo ============================================
echo.
cd /d "%~dp0"

echo [1/2] Instalando dependencias...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo.
    echo ERROR: No se pudieron instalar las dependencias.
    echo Asegurate de tener Python y pip instalados.
    pause
    exit /b 1
)

echo [2/2] Iniciando servidor...
echo.
echo  URL: http://localhost:8000
echo  Presiona Ctrl+C para detener
echo.
python -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
