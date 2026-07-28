@echo off
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate
python -m pip install -r requirements.txt
if "%ADMIN_USERNAME%"=="" set ADMIN_USERNAME=admin
if "%ADMIN_PASSWORD%"=="" set ADMIN_PASSWORD=Admin123!
if "%SECRET_KEY%"=="" set SECRET_KEY=clave-local-cambiar
python app.py
pause
