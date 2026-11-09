"""JRA-VAN Data Lab 連携 (64bit 側)。

JV-Link は 32bit COM のため、32bit Python のサブプロセスで
keiba/jvbridge.py を実行し、進捗を中継する。

CLI:
  python -m keiba.jvlink settings          # JV-Link 設定画面 (利用キー入力)
  python -m keiba.jvlink race 2024-01-01   # レース成績を取得
  python -m keiba.jvlink horse 2024-01-01  # 馬マスタ/血統を取得
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BRIDGE = Path(__file__).resolve().parent / "jvbridge.py"
CONFIG_PATH = PROJECT_ROOT / "data" / "jv_config.json"

SETUP_GUIDE = (
    "32bit Python が見つかりません。JV-Link は 32bit 専用のため、32bit 版 Python が必要です。\n"
    "1. https://www.python.org/downloads/windows/ から Windows installer (32-bit) を入手してインストール\n"
    "2. data/jv_config.json に {\"python32\": \"<python.exe のパス>\"} を保存\n"
    "   (既定パスにインストールした場合は自動検出されます)"
)


_PY32_CACHE = {}


def find_python32():
    """32bit Python の実行ファイルパスを探す。見つからなければ None。"""
    if "path" in _PY32_CACHE:
        return _PY32_CACHE["path"]
    path = _find_python32_uncached()
    _PY32_CACHE["path"] = path
    return path


def _find_python32_uncached():
    if CONFIG_PATH.exists():
        try:
            p = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("python32")
            if p and Path(p).exists():
                return p
        except (json.JSONDecodeError, OSError):
            pass

    candidates = []
    local = Path.home() / "AppData" / "Local" / "Programs" / "Python"
    if local.exists():
        candidates += sorted(local.glob("Python3*-32/python.exe"), reverse=True)
    candidates += sorted(Path("C:/").glob("Python3*-32/python.exe"), reverse=True)

    # py ランチャーからも探す
    try:
        out = subprocess.run(
            ["py", "-0p"], capture_output=True, text=True, timeout=15
        ).stdout
        for line in out.splitlines():
            if "-32" in line:
                path = line.split(None, 1)[-1].strip()
                if path.endswith("python.exe") and Path(path).exists():
                    candidates.append(Path(path))
    except (OSError, subprocess.TimeoutExpired):
        pass

    for c in candidates:
        return str(c)
    return None


def save_python32(path):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps({"python32": str(path)}, ensure_ascii=False), encoding="utf-8"
    )


def ensure_pywin32(py32):
    """32bit Python に pywin32 が無ければ入れる (結果はキャッシュする)。"""
    if _PY32_CACHE.get("pywin32"):
        return True
    chk = subprocess.run(
        [py32, "-c", "import win32com.client"], capture_output=True, timeout=60
    )
    if chk.returncode == 0:
        _PY32_CACHE["pywin32"] = True
        return True
    inst = subprocess.run(
        [py32, "-m", "pip", "install", "-q", "pywin32"],
        capture_output=True, text=True, timeout=600,
    )
    _PY32_CACHE["pywin32"] = inst.returncode == 0
    return _PY32_CACHE["pywin32"]


def run_bridge(args_list):
    """ブリッジをサブプロセスで実行し、出力行を yield するジェネレータ。"""
    py32 = find_python32()
    if py32 is None:
        raise RuntimeError(SETUP_GUIDE)
    yield f"32bit Python: {py32}"
    if not ensure_pywin32(py32):
        raise RuntimeError("32bit Python への pywin32 インストールに失敗しました。")

    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [py32, str(BRIDGE)] + args_list,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
        cwd=str(PROJECT_ROOT),
    )
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            tail.append(line)
            yield line
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(
            "JRA-VAN 取得が異常終了しました:\n" + "\n".join(tail[-5:])
        )


def collect_jv_race(from_date, setup=False):
    """レース成績 (RA+SE) を取得するジェネレータ。from_date: date か 'YYYY-MM-DD'。

    setup=True で過去データの一括取得 (通常モードは直近1年程度のみ)。
    """
    ymd = str(from_date).replace("-", "")
    args = ["race", ymd] + (["--setup"] if setup else [])
    yield from run_bridge(args)


def collect_jv_horse(from_date, setup=False):
    """馬マスタ/血統 (UM) を取得するジェネレータ。

    setup=True で全馬マスタの一括セットアップ (初回のみ必要、大容量)。
    """
    ymd = str(from_date).replace("-", "")
    args = ["horse", ymd] + (["--setup"] if setup else [])
    yield from run_bridge(args)


def fetch_jv_result(race_id, race_date):
    """速報データで当日レースの結果を取得する (中央のみ)。取得できたら True。"""
    ymd = str(race_date).replace("-", "")
    try:
        got = False
        for msg in run_bridge(["rtrace", race_id, ymd]):
            if "速報から結果を取得" in msg:
                got = True
        return got
    except RuntimeError:
        return False


def collect_jv_workout(from_date, setup=False):
    """坂路調教データを取得するジェネレータ。"""
    ymd = str(from_date).replace("-", "")
    args = ["workout", ymd] + (["--setup"] if setup else [])
    yield from run_bridge(args)


def jv_available():
    """JV-Link が使える状態か (利用キー設定済みか)。一度失敗したら諦める。"""
    return _PY32_CACHE.get("jv_ok", True)


def fetch_jv_odds(race_id, race_date):
    """速報オッズ (単勝) を {馬番: オッズ} で返す。取れなければ空 dict。"""
    if not jv_available():
        return {}
    ymd = str(race_date).replace("-", "")
    try:
        for msg in run_bridge(["rtodds", race_id, ymd]):
            if "利用キー" in msg or "code=-303" in msg:
                _PY32_CACHE["jv_ok"] = False
                return {}
            if msg.startswith("ODDS "):
                out = {}
                for part in msg[5:].split(","):
                    no, _, o = part.partition(":")
                    try:
                        out[int(no)] = float(o)
                    except ValueError:
                        continue
                return out
    except RuntimeError as e:
        if "-303" in str(e) or "利用キー" in str(e):
            _PY32_CACHE["jv_ok"] = False
    return {}


ENTRY_COLS = ["horse_no", "waku", "horse_id", "horse_name", "sex", "age",
              "kinryo", "jockey_id", "jockey_name", "weight", "weight_diff", "odds"]


def fetch_jv_entries_many(race_ids, race_date=None, db_path=None):
    """複数レースの出馬表を DB (entries テーブル) から読む。

    JV-Data の成績取得時に出馬表も保存しているため、JV への問い合わせは不要。
    返り値: {race_id: (レース情報dict or None, DataFrame)}
    """
    import pandas as pd

    from .scraper import DB_PATH, connect

    race_ids = list(race_ids)
    if not race_ids:
        return {}
    conn = connect(db_path or DB_PATH)
    try:
        ph = ",".join("?" * len(race_ids))
        ent = pd.read_sql(
            f"SELECT race_id, {', '.join(ENTRY_COLS)} FROM entries "
            f"WHERE race_id IN ({ph}) ORDER BY horse_no",
            conn, params=race_ids,
        )
        info_df = pd.read_sql(
            f"SELECT race_id, race_name, place, race_no, surface, distance, "
            f"going, date FROM races WHERE race_id IN ({ph})",
            conn, params=race_ids,
        )
    finally:
        conn.close()

    infos = {r["race_id"]: r.to_dict() for _, r in info_df.iterrows()}
    out = {}
    for rid in race_ids:
        sub = ent[ent["race_id"] == rid]
        out[rid] = (infos.get(rid), sub[ENTRY_COLS].reset_index(drop=True))
    return out


def fetch_jv_entries(race_id, race_date=None, db_path=None):
    """出馬表を DB から取得する。返り値: (レース情報dict or None, DataFrame)。"""
    import pandas as pd

    return fetch_jv_entries_many([race_id], race_date, db_path).get(
        race_id, (None, pd.DataFrame())
    )


def list_day_races(date, db_path=None):
    """指定日の中央レース一覧を返す (races テーブルを直接読む)。"""
    import pandas as pd

    from .scraper import DB_PATH, connect

    conn = connect(db_path or DB_PATH)
    try:
        return pd.read_sql(
            "SELECT race_id, place, race_no, race_name, surface, distance, "
            "post_time, grade, field_size FROM races WHERE date=? "
            "ORDER BY post_time, place, race_no",
            conn, params=(str(date),),
        )
    finally:
        conn.close()


def open_settings():
    """JV-Link の設定画面 (利用キー入力) を開く。閉じるまでブロックする。"""
    for msg in run_bridge(["settings"]):
        pass


def main():
    ap = argparse.ArgumentParser(description="JRA-VAN Data Lab 連携")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("settings", help="JV-Link 設定画面を開く")
    for name in ("race", "horse", "workout"):
        p = sub.add_parser(name)
        p.add_argument("from_date", help="YYYY-MM-DD")
        p.add_argument("--setup", action="store_true",
                       help="過去データの一括セットアップ取得 (初回のみ・大容量)")
    args = ap.parse_args()

    if args.cmd == "settings":
        open_settings()
        print("設定画面を閉じました。")
        return
    gen = {
        "race": collect_jv_race,
        "horse": collect_jv_horse,
        "workout": collect_jv_workout,
    }[args.cmd](args.from_date, setup=args.setup)
    try:
        for msg in gen:
            print(msg, flush=True)
    except RuntimeError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
