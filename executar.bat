@echo off
title Barbearia Manager - Servidor Local
echo ===================================================
echo     INICIANDO PROJETO FINAL - BARBEARIA MANAGER
echo ===================================================
echo.
echo [1/2] Verificando instalacao das dependencias...
python -m pip install -r requirements.txt
echo.
echo [2/2] Iniciando o servidor...
echo O sistema estara pronto em: http://127.0.0.1:5000
echo.
echo Abrindo seu navegador automaticamente...
start http://127.0.0.1:5000
python app.py
pause
