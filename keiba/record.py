"""予想履歴の管理と結果照合。"""
import pandas as pd

from .scraper import DB_PATH, JRA_PLACES, connect, scrape_race  # noqa: F401


def list_predictions(db_path=DB_PATH):
    conn = connect(db_path)
    try:
        return pd.read_sql(
            "SELECT race_id, race_date, place, race_no, race_name, "
            "MAX(created_at) AS created_at "
            "FROM predictions GROUP BY race_id "
            "ORDER BY race_date DESC, race_no DESC",
            conn,
        )
    finally:
        conn.close()


def check_result(race_id, db_path=DB_PATH):
    """保存済み予想と実際の結果を突き合わせる。

    返り値: (merged DataFrame, has_result bool)。
    結果が未確定 (まだ走っていない/未公開) なら has_result=False。
    """
    conn = connect(db_path)
    try:
        def load_res():
            return pd.read_sql(
                "SELECT horse_no, finish, odds, popularity FROM results WHERE race_id=?",
                conn, params=(race_id,),
            )

        res = load_res()
        if res.empty:
            # 中央 (JRA) は当日結果を JRA-VAN 速報から取る。
            # db.netkeiba.com は当日分の反映が遅く、空ページが返るため。
            if race_id[4:6] in JRA_PLACES:
                race_date = conn.execute(
                    "SELECT race_date FROM predictions WHERE race_id=? LIMIT 1",
                    (race_id,),
                ).fetchone()
                if race_date and race_date[0]:
                    from .jvlink import fetch_jv_result

                    if fetch_jv_result(race_id, race_date[0]):
                        res = load_res()

        if res.empty:
            # 結果行が無いのに取得済みマーカーだけ残っている場合があるので消してから取得
            conn.execute("DELETE FROM scraped WHERE race_id=?", (race_id,))
            conn.commit()
            scrape_race(conn, race_id)
            res = load_res()

        pred = pd.read_sql(
            "SELECT pred_rank, mark, horse_no, horse_name, p_top3, p_win "
            "FROM predictions WHERE race_id=? ORDER BY pred_rank",
            conn, params=(race_id,),
        )
        if res.empty:
            # 結果がまだ無い。'none' マークを消して後日再取得できるようにする
            conn.execute(
                "DELETE FROM scraped WHERE race_id=? AND status='none'", (race_id,)
            )
            conn.commit()
            return pred, False
        merged = pred.merge(res, on="horse_no", how="left")
        return merged, True
    finally:
        conn.close()


def honmei_stats(db_path=DB_PATH):
    """結果が判明しているレースでの ◎ (本命) の成績集計。"""
    conn = connect(db_path)
    try:
        df = pd.read_sql(
            "SELECT p.race_id, r.finish, r.odds "
            "FROM predictions p "
            "JOIN results r ON p.race_id = r.race_id AND p.horse_no = r.horse_no "
            "WHERE p.mark = '◎'",
            conn,
        )
    finally:
        conn.close()
    if df.empty:
        return None
    n = len(df)
    wins = df[df["finish"] == 1]
    return {
        "races": n,
        "win_rate": round(float((df["finish"] == 1).mean()), 3),
        "fukusho_rate": round(float((df["finish"] <= 3).mean()), 3),
        "tansho_roi": round(float(wins["odds"].sum() * 100 / (n * 100)), 3),
    }
