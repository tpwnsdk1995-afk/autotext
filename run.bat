@echo off
cd /d "%~dp0"

set "PYEXE=python"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

start http://localhost:8000
"%PYEXE%" server.py
pause
