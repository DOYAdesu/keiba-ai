@echo off
chcp 65001 > nul
title 出馬表の取り込み
cd /d "%~dp0"
echo.
echo  出馬表などを JRA-VAN から取り込んで、サイトを更新します。
echo.
echo    1 = 出馬表だけ (数分。木曜16時すぎ・金曜/土曜10時すぎの発表のあとに)
echo    2 = 自動更新と同じ全部 (netkeiba の補完・事前計算も。30分以上)
echo.
choice /c 12 /n /m " 番号を押してください [1/2]: "
if errorlevel 2 (
  powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\manual_update.ps1" -Mode full
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\manual_update.ps1" -Mode quick
)
echo.
echo  終わりました。このウィンドウは閉じてかまいません。
pause > nul
