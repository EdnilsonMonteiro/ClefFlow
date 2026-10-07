@echo off
setlocal
cd /d "%~dp0"
rem Use um ambiente separado do ClefFlow: torch e transformers sao pesados.
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv || (pause & exit /b 1)
  echo Instale o torch com CUDA para a sua placa (https://pytorch.org/get-started/locally/) e depois:
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause & exit /b 0
)
".venv\Scripts\python.exe" server.py --model Cloudflare/clef-flash --port 8321 --idle-unload 120 --quant int8
pause
