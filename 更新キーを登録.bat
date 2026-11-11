@echo off
chcp 65001 > nul
title 更新キーを Cloudflare に登録
cd /d "%~dp0"
echo.
echo  サイトの「更新」ボタン用の更新キー (data\update_token.txt) を
echo  Cloudflare Pages (keiba-ai) のシークレット UPDATE_TOKEN に登録します。
echo  キーは画面に表示しません。
echo.
npx --yes wrangler@4.42.0 pages secret put UPDATE_TOKEN --project-name keiba-ai < data\update_token.txt
echo.
echo  登録が終わったら、次のサイト公開から有効になります
echo  (「出馬表を取り込む.bat」の 1 を一度実行するか、18:30 の自動更新を待ってください)。
pause
