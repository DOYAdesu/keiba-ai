# サイトを自動更新する。タスクスケジューラから1日2回呼ばれる。
#
#   1. 終わったレースの結果を取り込む (新しいものだけ)
#   2. 予想を事前計算する
#   3. 木〜日は、これからのレースも予想する
#   4. 静的ファイルに書き出して Cloudflare Pages へ送る
#
# 月〜水に 3 をやらないのは、出馬表がまだ出ておらず、
# 探索が空振りして netkeiba に無駄な負荷をかけるため。

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
$log = Join-Path $root 'data\auto_update.log'
$lock = Join-Path $root 'data\auto_update.lock'

Set-Location $root
$env:PYTHONIOENCODING = 'utf-8'

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    # Write-Output だと Invoke-Step の戻り値に混ざって成功判定が狂うので Write-Host
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

# 前回の実行が終わっていなければ降りる (収集が長引くことがある)
if (Test-Path $lock) {
    $age = (Get-Date) - (Get-Item $lock).LastWriteTime
    if ($age.TotalHours -lt 6) {
        Write-Log "前回の実行がまだ動いているため中止 (ロックは $([int]$age.TotalMinutes) 分前)"
        exit 0
    }
    Write-Log "古いロックを無視して続行"
}
New-Item -ItemType File -Path $lock -Force | Out-Null

# ログが際限なく伸びないよう、たまに切り詰める
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 2MB)) {
    $keep = Get-Content $log -Tail 2000
    Set-Content -Path $log -Value $keep -Encoding utf8
}

function Invoke-Step($name, $file, $stepArgs) {
    Write-Log "開始: $name"
    & $file @stepArgs 2>&1 | ForEach-Object { Add-Content -Path $log -Value $_ -Encoding utf8 }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "失敗: $name (exit $LASTEXITCODE)"
        return $false
    }
    Write-Log "完了: $name"
    return $true
}

try {
    Write-Log '===== 自動更新 開始 ====='
    $year = (Get-Date).Year

    # 1. 終わったレースを取り込む。取得済みはスキップされるので週末の分だけ増える。
    #    別の収集 (過去分の一括取得など) が動いているときは、netkeiba に
    #    二重にアクセスしてブロックされるので見送る
    $scraping = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*keiba.scraper*' }
    if ($scraping) {
        Write-Log 'スキップ: 結果の取り込み (別の収集が実行中)'
    } else {
        Invoke-Step '結果の取り込み' $py @('-m', 'keiba.scraper', 'jra', "$year", "$year") | Out-Null
    }

    # 2. 予想の事前計算 (ここが失敗したら以降は無意味なので止める)
    if (-not (Invoke-Step '予想の事前計算' $py @('-m', 'web.build'))) { exit 1 }

    # 3. 出馬表が出ている時期だけ、これからのレースを予想する
    $dow = (Get-Date).DayOfWeek
    if ($dow -in 'Thursday', 'Friday', 'Saturday', 'Sunday') {
        Invoke-Step 'これからのレースの予想' $py @('-m', 'web.upcoming', '--next') | Out-Null
    } else {
        Write-Log "スキップ: これからのレースの予想 ($dow は出馬表が未発表)"
    }

    # 4. 書き出してデプロイ
    if (-not (Invoke-Step '静的ファイルの書き出し' $py @('-m', 'web.export'))) { exit 1 }
    Invoke-Step 'Cloudflare へデプロイ' 'npx' @(
        '--yes', 'wrangler@4.42.0', 'pages', 'deploy', 'dist',
        '--project-name', 'keiba-ai', '--branch', 'main', '--commit-dirty=true'
    ) | Out-Null

    Write-Log '===== 自動更新 終了 ====='
}
finally {
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
