"""開催日の当日更新。タスクスケジューラから15分ごとに呼ぶ (scripts/live_update.ps1)。

  1. JRA-VAN の速報を取り込む: 馬体重・取消・騎手変更・発走時刻・天候馬場・単複オッズ
     (発走90分前を切ったレースは全券種のオッズも)
  2. 馬体重が出たレースは当日モデルで予想し直す (それまでは前日モデル)
  3. 発走 25〜55 分前のレースは最新オッズで予想し直す (「発走40分前の再計算」)
  4. 終わったレースは速報から結果・払戻を取り込んでサイトに反映
  5. 変化があれば書き出して Cloudflare へ

    python -m web.live                 # 上の全部
    python -m web.live --import-only   # 1 だけ (今日と明日。朝夕の自動更新から呼ぶ)
    python -m web.live --no-deploy     # 書き出しまで
"""
import argparse
import datetime as dt
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# KEIBA_DB で別の DB を指定すると、書き出し・デプロイはしない (試験用)
DB = Path(os.environ.get("KEIBA_DB") or ROOT / "data" / "keiba.db")
TEST = bool(os.environ.get("KEIBA_DB"))
LOG = ROOT / "data" / "live_update.log"
LOCK = ROOT / "data" / "live_update.lock"
AUTO_LOCK = ROOT / "data" / "auto_update.lock"

WINDOW = (25, 55)       # 発走の何分前に予想し直すか (15分ごとに回すので幅は15分以上)
COMBO_AHEAD = 90        # 全券種のオッズを取るのは発走の何分前から
RESULT_AFTER = 12       # 発走の何分後から結果を取りに行くか
VALUE_LEAD = 8          # 直前の買い候補を発走の何分前に判定するか
VALUE_SPAN = (-3, 12)   # 1回の起動で受け持つ判定時刻の幅 (分)。15分ごとの起動で切れ目なく覆う


def log(msg):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _bridge(args):
    from keiba.jvlink import run_bridge
    out = []
    for m in run_bridge(args):
        out.append(m)
    return out


def ensure_tables(conn):
    from web.upcoming import _ensure_cols
    _ensure_cols(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS live_state ("
                 "race_id TEXT, kind TEXT, at TEXT, PRIMARY KEY (race_id, kind))")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS odds_live (
        race_id TEXT, at TEXT, kubun TEXT, win TEXT, pop TEXT, plo TEXT, phi TEXT,
        PRIMARY KEY (race_id, at));
    CREATE TABLE IF NOT EXISTS odds_combo (
        race_id TEXT, bet_type TEXT, combo TEXT, odds REAL, odds_hi REAL, pop INTEGER, at TEXT,
        PRIMARY KEY (race_id, bet_type, combo));
    """)


def day_races(conn, date):
    """その日の中央のレース (出馬表あり): [(race_id, 発走時刻 datetime or None)]"""
    rows = conn.execute(
        "SELECT r.race_id, r.post_time FROM races r WHERE r.date=? "
        "AND EXISTS (SELECT 1 FROM entries e WHERE e.race_id=r.race_id) ORDER BY r.post_time",
        (str(date),)).fetchall()
    out = []
    for rid, pt in rows:
        t = None
        if pt and len(pt) == 5:
            t = dt.datetime.combine(date, dt.time(int(pt[:2]), int(pt[3:])))
        out.append((rid, t))
    return out


def import_live(conn, date, now, race_ids, combo_ids):
    """速報を取り込む。返り値: ブリッジの集計行"""
    if not race_ids:
        return ""
    ymd = date.strftime("%Y%m%d")
    try:
        out = _bridge(["live", ymd, ",".join(race_ids), ",".join(combo_ids)])
    except RuntimeError as e:
        log(f"速報の取り込みに失敗: {str(e)[-200:]}")
        return ""
    stat = next((m for m in out if m.startswith("LIVE ")), "")
    log(f"{date} 速報: {len(race_ids)}レース (全券種 {len(combo_ids)}) {stat[5:]}")
    return stat


def fetch_results(conn, race_ids, date):
    """終わったレースの結果・払戻を速報から取る。取れた race_id の list"""
    got = []
    ymd = date.strftime("%Y%m%d")
    for rid in race_ids:
        try:
            out = _bridge(["rtrace", rid, ymd])
        except RuntimeError as e:
            log(f"{rid} 結果の取得に失敗: {str(e)[-120:]}")
            continue
        if any("速報から結果を取得" in m for m in out):
            got.append(rid)
    if got:
        log(f"結果を取り込み: {len(got)}レース")
    return got


def settle(conn, race_ids):
    """結果が入ったレースを、サイトの表 (site_preds / site_races) に反映する。

    予想そのもの (印・確率) は発走前のまま残し、着順・払戻だけを書き足す。
    夜の自動更新 (web.build) で正式に作り直される。
    """
    for rid in race_ids:
        res = conn.execute(
            "SELECT horse_no, finish, time_sec, agari, corner1, popularity, odds, horse_name "
            "FROM results WHERE race_id=?", (rid,)).fetchall()
        if not res:
            continue
        for no, fin, t, ag, c1, pop, odds, _ in res:
            conn.execute("UPDATE site_preds SET finish=?, time_sec=?, agari=?, corner1=?, "
                         "popularity=COALESCE(?, popularity) WHERE race_id=? AND horse_no=?",
                         (fin, t, ag, c1, pop, rid, no))
        win = [r for r in res if r[1] == 1]
        top = conn.execute("SELECT horse_no FROM site_preds WHERE race_id=? AND rank=1",
                           (rid,)).fetchone()
        top_fin = next((r[1] for r in res if top and r[0] == top[0]), None)
        fuku = conn.execute("SELECT pay FROM payouts WHERE race_id=? AND bet_type='複勝' "
                            "AND CAST(combo AS INT)=?", (rid, top[0] if top else -1)).fetchone()
        tri = conn.execute("SELECT MAX(pay) FROM payouts WHERE race_id=? AND bet_type='三連単'",
                           (rid,)).fetchone()
        conn.execute(
            "UPDATE site_races SET winner_no=?, winner_name=?, top_finish=?, "
            "top_fuku_pay=?, trifecta_pay=? WHERE race_id=?",
            (win[0][0] if win else None, win[0][7] if win else None, top_fin,
             fuku[0] if fuku else None, tri[0] if tri else None, rid))
        conn.execute("INSERT OR REPLACE INTO live_state VALUES (?, 'result', ?)",
                     (rid, dt.datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()


_CTX = {}


def repredict(date, race_ids, stage):
    """予想し直して該当レースだけ差し替える。"""
    from web import upcoming
    if not race_ids:
        return 0
    if "ctx" not in _CTX:   # モデルと過去成績の読み込みは1分ほどかかるので1回だけ
        _CTX["ctx"] = upcoming.load_ctx(DB)
    res = upcoming.run(date, db_path=DB, race_ids=race_ids, stage=stage, source="jv", only=True,
                       progress=lambda m: None, ctx=_CTX["ctx"])
    log(f"予想し直し ({stage}): {res.get('races', 0)}レース "
        + ", ".join(r[-4:] for r in race_ids))
    return res.get("races", 0)


def value_watch(date, races, now, test=False, deploy=True):
    """発走 VALUE_LEAD 分前に、最新オッズで「直前の買い候補」を判定する (keiba/value.py)。

    判定時刻がこの起動の受け持ち (今から -3〜+12分) に入るレースを、時刻が来るまで待って順に判定する。
    候補が出たら、その日のファイルだけ書き換えてすぐ公開する。
    """
    import time as _time

    from keiba import value

    conn = sqlite3.connect(DB, timeout=120)
    try:
        conn.executescript(value.VALUE_SCHEMA)
        checked = {r[0] for r in conn.execute("SELECT race_id FROM value_checked")}
        lo = now + dt.timedelta(minutes=VALUE_SPAN[0])
        hi = now + dt.timedelta(minutes=VALUE_SPAN[1])
        todo = sorted([(t - dt.timedelta(minutes=VALUE_LEAD), rid, t) for rid, t in races
                       if t is not None and rid not in checked
                       and lo <= t - dt.timedelta(minutes=VALUE_LEAD) <= hi])
        for at, rid, post in todo:
            if not test:
                wait = (at - dt.datetime.now()).total_seconds()
                if wait > 0:
                    _time.sleep(wait)
            got = import_live(conn, date, at, [rid], [])
            if not got and not test:
                # 取れなかった (メンテナンスなど)。判定済みにせず、次の起動で間に合えば取り直す
                log(f"直前の判定 {rid[-4:]}: 最新オッズが取れないため見送り")
                continue
            n = value.judge(conn, rid, date, post, now=at if test else None)
            log(f"直前の判定 {rid[-4:]} (発走{post:%H:%M}): 候補 {n}頭")
            if n and not test:
                from web.export import patch_value_picks
                if patch_value_picks(str(date)):
                    deploy_only(deploy)
    finally:
        conn.close()


def deploy_only(deploy=True):
    if not deploy:
        return True
    r = subprocess.run(
        "npx --yes wrangler@4.42.0 pages deploy dist --project-name keiba-ai "
        "--branch main --commit-dirty=true",
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=True)
    log("デプロイ完了 (直前の買い候補)" if r.returncode == 0 else "デプロイ失敗: " + (r.stdout + r.stderr)[-300:])
    return r.returncode == 0


def export_deploy(deploy=True):
    py = sys.executable
    r = subprocess.run([py, "-m", "web.export"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log("書き出しに失敗: " + r.stdout[-300:] + r.stderr[-300:])
        return False
    if not deploy:
        log("書き出し完了 (デプロイなし)")
        return True
    r = subprocess.run(
        "npx --yes wrangler@4.42.0 pages deploy dist --project-name keiba-ai "
        "--branch main --commit-dirty=true",
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=True)
    ok = r.returncode == 0
    log("デプロイ完了" if ok else "デプロイ失敗: " + (r.stdout + r.stderr)[-300:])
    return ok


def run(now=None, import_only=False, deploy=True, force=False):
    now = now or dt.datetime.now()
    today = now.date()
    conn = sqlite3.connect(DB, timeout=120)
    ensure_tables(conn)
    changed = False
    try:
        dates = [today, today + dt.timedelta(days=1)] if import_only else [today]
        for date in dates:
            races = day_races(conn, date)
            if not races:
                continue
            pending = [rid for rid, t in races if t is None or t > now - dt.timedelta(minutes=2)]
            combo = [rid for rid, t in races
                     if t is not None and now <= t <= now + dt.timedelta(minutes=COMBO_AHEAD)]
            if import_live(conn, date, now, pending, combo):
                changed = True
                # 発走時刻の変更をサイトの表にも
                conn.execute("UPDATE site_races SET post_time=(SELECT post_time FROM races r "
                             "WHERE r.race_id=site_races.race_id) WHERE date=?", (str(date),))
                conn.commit()
            if import_only or date != today:
                continue

            # 終わったレースの結果
            have = {r[0] for r in conn.execute(
                "SELECT DISTINCT race_id FROM results WHERE date=?", (str(date),))}
            done = {r[0] for r in conn.execute(
                "SELECT race_id FROM live_state WHERE kind='result'")}
            finished = [rid for rid, t in races
                        if t is not None and t + dt.timedelta(minutes=RESULT_AFTER) <= now
                        and rid not in done]
            need = [rid for rid in finished if rid not in have]
            got = fetch_results(conn, need, date) if need else []
            to_settle = [rid for rid in finished if rid in have or rid in got]
            if to_settle:
                settle(conn, to_settle)
                changed = True

            # 予想し直し: 40分前 (最新オッズ) と、馬体重が出て当日モデルに切り替わるとき
            state = {(r[0], r[1]) for r in conn.execute("SELECT race_id, kind FROM live_state")}
            # 朝夕の自動更新が遅れて走ると予想が出馬表段階に戻るので、記録ではなく今の段階で見る
            stage = dict(conn.execute("SELECT race_id, stage FROM site_races WHERE date=?", (str(date),)))
            win = [rid for rid, t in races if t is not None
                   and WINDOW[0] <= (t - now).total_seconds() / 60 < WINDOW[1]
                   and (force or stage.get(rid) != "直前")]
            pre_model = {r[0] for r in conn.execute(
                "SELECT race_id FROM site_races WHERE date=? AND model='前日モデル'", (str(date),))}
            weighed = {r[0] for r in conn.execute(
                "SELECT race_id FROM entries WHERE race_id IN (SELECT race_id FROM races WHERE date=?) "
                "GROUP BY race_id HAVING SUM(weight IS NOT NULL) * 2 >= COUNT(*)", (str(date),))}
            switch = [rid for rid in pending if rid in pre_model and rid in weighed and rid not in win
                      and stage.get(rid) != "直前"]
            if win or switch:
                n = repredict(date, win, "直前") if win else 0
                n += repredict(date, switch, "当日") if switch else 0
                stamp = now.strftime("%Y-%m-%d %H:%M")
                conn.executemany("INSERT OR REPLACE INTO live_state VALUES (?, 't40', ?)",
                                 [(rid, stamp) for rid in win])
                conn.commit()
                changed = changed or n > 0
    finally:
        conn.close()
    if changed and not import_only and not TEST:
        export_deploy(deploy)
    if not import_only:
        races = day_races(sqlite3.connect(DB), today)
        if races:
            value_watch(today, races, now, test=TEST, deploy=deploy)
    return changed


def main():
    ap = argparse.ArgumentParser(description="開催日の当日更新")
    ap.add_argument("--import-only", action="store_true", help="速報の取り込みだけ (今日と明日)")
    ap.add_argument("--no-deploy", action="store_true")
    ap.add_argument("--force", action="store_true", help="窓の中のレースを必ず予想し直す")
    ap.add_argument("--now", help="時刻を指定して試す 'YYYY-MM-DD HH:MM'")
    a = ap.parse_args()
    if AUTO_LOCK.exists() and not a.import_only:
        age = dt.datetime.now().timestamp() - AUTO_LOCK.stat().st_mtime
        if age < 6 * 3600:
            print("自動更新が実行中のため見送り")
            return
    if LOCK.exists() and not a.import_only:
        age = dt.datetime.now().timestamp() - LOCK.stat().st_mtime
        if age < 40 * 60:
            print("前回の当日更新がまだ動いているため見送り")
            return
    now = dt.datetime.strptime(a.now, "%Y-%m-%d %H:%M") if a.now else None
    if not a.import_only:
        LOCK.write_text(str(dt.datetime.now()))
    try:
        run(now, import_only=a.import_only, deploy=not a.no_deploy, force=a.force)
    finally:
        if not a.import_only and LOCK.exists():
            LOCK.unlink()


if __name__ == "__main__":
    main()
