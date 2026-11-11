"""2016〜2019年の中央のデータを集める (JRA-VAN のメンテナンス明けに始める)。

  1. 指定時刻まで待つ (既定 18:05。JRA-VAN の 2026-11-10 のメンテナンスは 8:00〜18:00)
  2. netkeiba で成績・払戻 (2016〜2019)。ブロックされたら2時間待って続きから
  3. 調教師・ラップの抜け、払戻の抜けを netkeiba で補う
  4. JRA-VAN の時系列オッズ (前日最終・40分前・5分前など)
  5. 学習の始まりを 2016年 にするか 2020年 のままにするかを比べる (scripts/refine_train_from.py)

netkeiba は同時に2本走らせるとブロックされるので、ほかの収集が動いていれば終わるまで待つ。
JRA-VAN の取得は自動更新のロックが立っている間は待つ (jvbridge の _wait_for_locks)。

  python scripts/collect_2016.py [HH:MM]
"""
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
LOG = ROOT / "data" / "collect_2016.log"
Y1, Y2 = 2016, 2019


def say(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def other_scraper_running():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*keiba.scraper*' } | Measure-Object).Count"],
        capture_output=True, text=True).stdout.strip()
    return out not in ("", "0")


def run_step(name, args, retries=6, block_wait=2 * 3600):
    for attempt in range(1, retries + 1):
        while other_scraper_running():
            say(f"{name}: ほかの netkeiba 収集が動いているので待機")
            time.sleep(10 * 60)
        say(f"{name}: 開始 (試行{attempt})")
        with open(ROOT / "data" / f"collect_2016_{name}.log", "a", encoding="utf-8") as f:
            p = subprocess.run([str(PY)] + args, cwd=str(ROOT), env=ENV, stdout=f,
                               stderr=subprocess.STDOUT)
        tail = (ROOT / "data" / f"collect_2016_{name}.log").read_text(encoding="utf-8", errors="replace")[-400:]
        if p.returncode == 0 and "中断:" not in tail:
            say(f"{name}: 完了")
            return True
        wait = block_wait if ("中断:" in tail or p.returncode == 2) else 20 * 60
        say(f"{name}: 途中で止まりました (code={p.returncode})。{wait // 60}分待って続きから")
        time.sleep(wait)
    say(f"{name}: 打ち切り")
    return False


def main():
    at = sys.argv[1] if len(sys.argv) > 1 else "18:05"
    hh, mm = map(int, at.split(":"))
    start = dt.datetime.combine(dt.date.today(), dt.time(hh, mm))
    if dt.datetime.now() < start:
        say(f"{start:%H:%M} まで待ってから始めます")
        while dt.datetime.now() < start:
            time.sleep(30)

    run_step("results", ["-m", "keiba.scraper", "jra", str(Y1), str(Y2)])
    run_step("backfill", ["-m", "keiba.scraper", "backfill"])
    run_step("payouts", ["-m", "keiba.scraper", "payouts", "--limit", "30000",
                         "--since", f"{Y1}-01-01"])

    # JRA-VAN の時系列オッズ (自動更新のロックが立っている間は待つ)
    say("時系列オッズ: 開始")
    import sqlite3
    conn = sqlite3.connect(ROOT / "data" / "keiba.db")
    try:
        done = {r[0] for r in conn.execute("SELECT race_id FROM odds_hist WHERE tag IN ('fin','none')")}
        rows = conn.execute("SELECT DISTINCT race_id, date FROM results WHERE is_nar=0 AND date>=? "
                            "AND date<'2020-01-01' ORDER BY date DESC", (f"{Y1}-01-01",)).fetchall()
    finally:
        conn.close()
    todo = [f"{r}\t{d.replace('-', '')}" for r, d in rows if r not in done]
    path = ROOT / "data" / "oddshist_2016.txt"
    path.write_text("\n".join(todo), encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    from keiba.jvlink import run_bridge
    try:
        with open(ROOT / "data" / "collect_2016_odds.log", "a", encoding="utf-8") as f:
            for m in run_bridge(["oddshist", str(path)]):
                f.write(m + "\n")
                f.flush()
        say(f"時系列オッズ: 完了 ({len(todo)}レース)")
    except RuntimeError as e:
        say(f"時系列オッズ: 失敗 {str(e)[-200:]}")

    # 学習の始まりを比べる
    say("学習期間の比較: 開始")
    with open(ROOT / "data" / "refine_train_from.log", "w", encoding="utf-8") as f:
        subprocess.run([str(PY), str(ROOT / "scripts" / "refine_train_from.py")], cwd=str(ROOT),
                       env=ENV, stdout=f, stderr=subprocess.STDOUT)
    say("すべて完了")


if __name__ == "__main__":
    main()
