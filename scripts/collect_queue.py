"""ほかの netkeiba 収集が終わるのを待ってから、抜けている年を収集する。

2025年の中央は開催日の一部が欠けているので、2020〜2024年の収集が終わったあとに続けて取る。
同時に2本走らせるとブロックされやすいので、必ず1本ずつ。

使い方: python scripts/collect_queue.py 2025 2025
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
LOG = ROOT / "data" / "collect_queue.log"


def say(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def other_scraper_running():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*keiba.scraper*' } | Measure-Object).Count"],
        capture_output=True, text=True).stdout.strip()
    return out not in ("", "0")


def main():
    y1, y2 = (sys.argv[1:3] + [None, None])[:2]
    y1 = y1 or "2025"
    y2 = y2 or y1
    say(f"待機開始: jra {y1} {y2}")
    while other_scraper_running():
        time.sleep(10 * 60)
    for attempt in range(1, 7):
        say(f"収集 jra {y1} {y2} 試行{attempt}")
        with open(ROOT / "data" / f"collect_{y1}_{y2}.log", "a", encoding="utf-8") as f:
            code = subprocess.run([str(PY), "-m", "keiba.scraper", "jra", y1, y2],
                                  cwd=str(ROOT), env=ENV, stdout=f,
                                  stderr=subprocess.STDOUT).returncode
        if code == 0:
            say("完了")
            return
        pause = 2 * 60 * 60 if code == 2 else 20 * 60   # code=2 はブロック
        say(f"code={code}。{pause // 60}分待って再開")
        time.sleep(pause)
    say("打ち切り")


if __name__ == "__main__":
    main()
