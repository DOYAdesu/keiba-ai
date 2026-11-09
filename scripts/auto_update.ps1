# サイトを自動更新する。タスクスケジューラから1日2回呼ばれる。
#
#   1. JRA-VAN から直近2週間の成績・払戻・出馬表を取り込む (公式データ)
#   2. netkeiba で取りこぼしを補う (JRA-VAN の契約が切れていても動くように)
#   3. 予想を事前計算する
#   4. これからの1週間 (土日両方) を予想する。出馬表前は特別登録で
#   5. 静的ファイルに書き出して Cloudflare Pages へ送る
#
# JRA-VAN の取得 (32bit の JV-Link) は途中で強制終了すると利用キーの設定が
# 消えることがあるので、このスクリプトからは絶対に止めない。

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
    # 1月は前年12月の開催も拾う
    $yearFrom = if ((Get-Date).Month -eq 1) { $year - 1 } else { $year }

    # 1. JRA-VAN (公式)。成績・払戻に加えて、出走前のレースの出馬表と発走時刻も入る
    $since = (Get-Date).AddDays(-14).ToString('yyyy-MM-dd')
    $jvOk = Invoke-Step 'JRA-VAN から取り込み' $py @('-m', 'keiba.jvlink', 'race', $since)
    if ($jvOk) {
        # 出馬表が出る前 (日〜水) でも、特別レースの登録馬は分かる
        Invoke-Step '特別登録の取り込み' $py @('-m', 'keiba.jvlink', 'toku', $since) | Out-Null
    }
    if (-not $jvOk) {
        Write-Log 'JRA-VAN が使えないため netkeiba だけで更新します (利用キーの期限を確認してください)'
    }

    # 2. netkeiba で取りこぼしを補う。取得済みはスキップされるので軽い。
    #    別の収集が動いているときは、二重アクセスでブロックされるので見送る
    $scraping = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*keiba.scraper*' }
    if ($scraping) {
        Write-Log 'スキップ: netkeiba の取り込み (別の収集が実行中)'
    } else {
        Invoke-Step 'netkeiba で取りこぼしを補う' $py @('-m', 'keiba.scraper', 'jra', "$yearFrom", "$year") | Out-Null
        # 払戻の抜け (2020年・2025年など) を netkeiba から少しずつ補う。
        # 100万馬券の法則を見つけるのに使う。一度に取りすぎるとブロックされるので150件ずつ
        Invoke-Step '払戻の補完' $py @('-m', 'keiba.scraper', 'payouts', '--limit', '150', '--since', '2020-01-01') | Out-Null
    }

    # 3a. 週に1度 (月曜の朝)、増えたデータでAIを学習し直し、100万馬券の法則も見直す
    if ((Get-Date).DayOfWeek -eq 'Monday' -and (Get-Date).Hour -lt 12) {
        $model = Join-Path $root 'models\model.pkl'
        Copy-Item $model (Join-Path $root 'models\model_prev.pkl') -Force -ErrorAction SilentlyContinue
        Invoke-Step 'AIの再学習' $py @('-m', 'keiba.train') | Out-Null
        Invoke-Step '100万馬券の法則の見直し' $py @((Join-Path $root 'scripts\million_rules.py'), '--save') | Out-Null
    }

    # 3. 予想の事前計算 (ここが失敗したら以降は無意味なので止める)
    if (-not (Invoke-Step '予想の事前計算' $py @('-m', 'web.build'))) { exit 1 }

    # 4. これからの1週間 (土日の両方) を予想する。
    #    出馬表が出ている日は全レース、まだの日は特別登録の特別レースだけ。
    Invoke-Step 'これからの1週間の予想' $py @('-m', 'web.upcoming', '--week') | Out-Null
    # JRA-VAN が使えないときは netkeiba の出馬表を探す (出馬表が出る木〜日だけ)
    $dow = (Get-Date).DayOfWeek
    if (-not $jvOk -and $dow -in 'Thursday', 'Friday', 'Saturday', 'Sunday') {
        Invoke-Step 'netkeiba で直近の開催を予想' $py @('-m', 'web.upcoming', '--next') | Out-Null
    }

    # 5. 書き出してデプロイ
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
