# 開催日の当日更新。タスクスケジューラから15分ごとに呼ばれる (KeibaLiveUpdate)。
#
#   馬体重・取消・騎手変更・発走時刻・オッズを JRA-VAN の速報から取り込み、
#   馬体重が出たら当日モデル、発走40分前には最新オッズで予想し直す。
#   終わったレースは結果を入れる。開催の無い日は何もせずに終わる。
#
# JRA-VAN の取得 (32bit の JV-Link) は途中で強制終了すると利用キーの設定が
# 消えることがあるので、このスクリプトからは止めない。

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
Set-Location $root
$env:PYTHONIOENCODING = 'utf-8'

# 開催があるのは土日と祝日。9時前と17時以降は何もしない
$h = (Get-Date).Hour
if ($h -lt 9 -or $h -ge 17) { exit 0 }

& $py -m web.live 2>&1 | Out-Null
exit 0
