"""取りこぼしを順に埋める。netkeiba は同時に2本走らせるとブロックされるので1本ずつ。

    python scripts/collect_fix.py

1. 2024年の取りこぼし (12月の関西開催が通信エラーで抜けていた)
2. 払戻の抜け (2020年がほとんど未取得。100万馬券の分析に使う)
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
LOG = ROOT / "data" / "collect_fix.log"

STEPS = [
    ("2024年の取りこぼし", ["-m", "keiba.scraper", "jra", "2024", "2024"], "collect_2024_fix.log"),
    ("払戻の抜け", ["-m", "keiba.scraper", "payouts", "--limit", "3000",
                    "--since", "2020-01-01"], "collect_payouts.log"),
]


def say(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def main():
    for name, args, logname in STEPS:
        for attempt in range(1, 5):
            say(f"{name} 試行{attempt}")
            with open(ROOT / "data" / logname, "a", encoding="utf-8") as f:
                code = subprocess.run([str(PY), *args], cwd=str(ROOT), env=ENV,
                                      stdout=f, stderr=subprocess.STDOUT).returncode
            if code == 0:
                say(f"{name} 完了")
                break
            pause = 2 * 60 * 60 if code == 2 else 15 * 60   # code=2 はブロック
            say(f"{name} code={code}。{pause // 60}分待って再開")
            time.sleep(pause)
        else:
            say(f"{name} 打ち切り")
    say("すべて終了")


if __name__ == "__main__":
    main()
