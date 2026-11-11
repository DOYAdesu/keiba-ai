@echo off
cd /d "%~dp0"
start "keiba update poller" /min .venv\Scripts\pythonw.exe scripts\update_poller.py
