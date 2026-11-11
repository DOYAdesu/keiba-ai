# 出馬表などを手動で取り込んでサイトを更新する (出馬表.bat から呼ぶ)。
#
#   -Mode quick : JRA-VAN の出馬表・特別登録・当日速報 → これからの予想 → 書き出し → デプロイ (数分)
#   -Mode full  : 自動更新 (scripts/auto_update.ps1) をそのまま今すぐ走らせる (netkeiba・事前計算も。時間がかかる)
#
# 自動更新と同じロック (data/auto_update.lock) を使うので、自動更新・当日更新・時系列オッズの収集とは
# JV-Link を同時に使わない。JV-Link は途中で強制終了しないこと (利用キーの設定が消えることがある)。
param([ValidateSet('quick', 'full')][string]$Mode = 'quick')

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
$log = Join-Path $root 'data\manual_update.log'
$lock = Join-Path $root 'data\auto_update.lock'
$liveLock = Join-Path $root 'data\live_update.lock'
Set-Location $root
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line -ForegroundColor Cyan
    try { Add-Content -Path $log -Value $line -Encoding utf8 -ErrorAction Stop } catch {}
}

if ($Mode -eq 'full') {
    Write-Log '自動更新と同じ処理を今すぐ実行します (30分以上かかることがあります)'
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'auto_update.ps1')
    Write-Log "終わりました。くわしくは data\auto_update.log"
    exit $LASTEXITCODE
}

function Invoke-Step($name, $file, $stepArgs) {
    Write-Log "開始: $name"
    $script:lastOut = @()
    & $file @stepArgs 2>&1 | ForEach-Object {
        $s = "$_"
        $script:lastOut += $s
        try { Add-Content -Path $log -Value $s -Encoding utf8 -ErrorAction Stop } catch {}
        Write-Host "  $s"
    }
    $ok = ($LASTEXITCODE -eq 0)
    Write-Log ($(if ($ok) { "完了: $name" } else { "失敗: $name (exit $LASTEXITCODE)" }))
    return $ok
}

# 自動更新が動いていれば、それが終わればサイトも新しくなるので降りる
if (Test-Path $lock) {
    $age = (Get-Date) - (Get-Item $lock).LastWriteTime
    if ($age.TotalHours -lt 6) {
        Write-Log "自動更新がいま動いています ($([int]$age.TotalMinutes)分前に開始)。終わるとサイトも更新されるので、今回は取り込みません"
        exit 0
    }
}
# 土日の当日更新 (15分ごと) が JV-Link を使っている間は待つ (ふつう数分)
$waited = 0
while ((Test-Path $liveLock) -and $waited -lt 20) {
    if ($waited -eq 0) { Write-Log '当日更新が動いているので、終わるまで待ちます' }
    Start-Sleep -Seconds 30
    $waited++
}

New-Item -ItemType File -Path $lock -Force | Out-Null
try {
    Write-Log '===== 手動の取り込み 開始 ====='
    # 時系列オッズの収集が動いていれば、JV-Link を手放すまで少し待つ (1レースずつ確認している)
    Start-Sleep -Seconds 10
    $since = (Get-Date).AddDays(-3).ToString('yyyy-MM-dd')
    $jvOk = Invoke-Step 'JRA-VAN から出馬表・成績を取り込み' $py @('-m', 'keiba.jvlink', 'race', $since)
    if ($jvOk) {
        Invoke-Step '特別登録の取り込み' $py @('-m', 'keiba.jvlink', 'toku', $since) | Out-Null
        Invoke-Step '当日速報 (馬体重・取消・オッズ) の取り込み' $py @('-m', 'web.live', '--import-only') | Out-Null
    } else {
        $last = $script:lastOut | Select-String 'code=(-\d+)' | Select-Object -Last 1
        $code = if ($last) { $last.Matches[0].Groups[1].Value } else { '?' }
        $why = switch ($code) { '-504' { 'JRA-VAN のメンテナンス中' } { $_ -in '-301','-302','-303' } { '利用キーの問題' } default { "code=$code" } }
        Write-Log "JRA-VAN から取り込めませんでした ($why)。取り込み済みのデータで予想だけ作り直します"
    }
    if (-not (Invoke-Step 'これからの1週間の予想' $py @('-m', 'web.upcoming', '--week'))) { exit 1 }
    if (-not (Invoke-Step 'サイトの書き出し' $py @('-m', 'web.export'))) { exit 1 }
    if (Invoke-Step 'Cloudflare へデプロイ' 'npx' @('--yes', 'wrangler@4.42.0', 'pages', 'deploy', 'dist',
            '--project-name', 'keiba-ai', '--branch', 'main', '--commit-dirty=true')) {
        Write-Log '===== 完了: 1分ほどで https://keiba-ai-3ex.pages.dev に反映されます ====='
    }
}
finally {
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
