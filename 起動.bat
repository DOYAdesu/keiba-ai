@echo off
title Keiba Yosou AI
cd /d "%~dp0"
echo Starting Keiba Yosou AI ... browser will open shortly.
echo Close this window to stop the app.
.venv\Scripts\streamlit.exe run app.py --server.port 8501
pause
