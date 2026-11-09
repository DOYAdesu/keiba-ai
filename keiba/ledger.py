"""収支管理: 実際に購入した馬券を記録し、払戻を自動計算する。

払戻は JRA-VAN の payouts テーブル (実際の配当) と照合して確定させる。
"""
import datetime as dt

import pandas as pd

from .scraper import DB_PATH, connect

BET_TYPES = ["単勝", "複勝", "馬連", "ワイド", "馬単", "三連複", "三連単", "枠連"]


def _ensure(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id TEXT, race_date TEXT, place TEXT, race_no INTEGER,
            race_name TEXT, bet_type TEXT, combo TEXT, amount INTEGER,
            payout INTEGER, settled INTEGER DEFAULT 0, created_at TEXT
        );
        """
    )


def normalize_combo(bet_type, combo):
    """組番を払戻データと同じ形式 (2桁ゼロ埋め・順不同は昇順) に整える。"""
    nums = [n for n in str(combo).replace("－", "-").replace(" ", "").split("-") if n]
    try:
        nums = [int(n) for n in nums]
    except ValueError:
        return str(combo)
    if bet_type in ("馬単", "三連単"):
        pass  # 着順どおりなので並べ替えない
    else:
        nums = sorted(nums)
    return "".join(f"{n:02d}" for n in nums)


def add_ticket(race_id, bet_type, combo, amount, db_path=DB_PATH):
    """馬券を1点記録する。"""
    conn = connect(db_path)
    try:
        _ensure(conn)
        info = conn.execute(
            "SELECT date, place, race_no, race_name FROM races WHERE race_id=?",
            (race_id,),
        ).fetchone()
        if info is None:
            info = conn.execute(
                "SELECT race_date, place, race_no, race_name FROM predictions "
                "WHERE race_id=? LIMIT 1", (race_id,)
            ).fetchone()
        date, place, race_no, race_name = info or (None, None, None, None)
        conn.execute(
            "INSERT INTO tickets (race_id, race_date, place, race_no, race_name, "
            "bet_type, combo, amount, payout, settled, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,NULL,0,?)",
            (race_id, date, place, race_no, race_name, bet_type,
             normalize_combo(bet_type, combo), int(amount),
             dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
        conn.commit()
    finally:
        conn.close()


def delete_ticket(ticket_id, db_path=DB_PATH):
    conn = connect(db_path)
    try:
        _ensure(conn)
        conn.execute("DELETE FROM tickets WHERE id=?", (int(ticket_id),))
        conn.commit()
    finally:
        conn.close()


def settle(db_path=DB_PATH):
    """未確定の馬券を payouts と照合して払戻を確定する。確定した件数を返す。"""
    conn = connect(db_path)
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT id, race_id, bet_type, combo, amount FROM tickets WHERE settled=0"
        ).fetchall()
        n = 0
        for tid, rid, bet_type, combo, amount in rows:
            has_race = conn.execute(
                "SELECT 1 FROM payouts WHERE race_id=? LIMIT 1", (rid,)
            ).fetchone()
            if not has_race:
                continue  # まだ結果が無い
            hit = conn.execute(
                "SELECT pay FROM payouts WHERE race_id=? AND bet_type=? AND combo=?",
                (rid, bet_type, combo),
            ).fetchone()
            payout = int(hit[0] * amount / 100) if hit else 0
            conn.execute(
                "UPDATE tickets SET payout=?, settled=1 WHERE id=?", (payout, tid)
            )
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def list_tickets(db_path=DB_PATH):
    conn = connect(db_path)
    try:
        _ensure(conn)
        return pd.read_sql(
            "SELECT id, race_date, place, race_no, race_name, bet_type, combo, "
            "amount, payout, settled FROM tickets ORDER BY race_date DESC, id DESC",
            conn,
        )
    finally:
        conn.close()


def summary(db_path=DB_PATH):
    """全体と月別の収支をまとめる。"""
    df = list_tickets(db_path)
    if df.empty:
        return None
    settled = df[df["settled"] == 1]
    total = {
        "点数": len(df),
        "確定点数": len(settled),
        "投資額": int(df["amount"].sum()),
        "払戻額": int(settled["payout"].sum()) if len(settled) else 0,
    }
    stake = int(settled["amount"].sum()) if len(settled) else 0
    total["収支"] = total["払戻額"] - stake
    total["回収率"] = (total["払戻額"] / stake) if stake else None
    total["的中率"] = (settled["payout"] > 0).mean() if len(settled) else None

    by_month = pd.DataFrame()
    by_type = pd.DataFrame()
    if len(settled):
        s = settled.copy()
        s["月"] = s["race_date"].str[:7]
        by_month = s.groupby("月").apply(
            lambda g: pd.Series({
                "点数": len(g), "投資額": int(g["amount"].sum()),
                "払戻額": int(g["payout"].sum()),
                "収支": int(g["payout"].sum() - g["amount"].sum()),
                "回収率": g["payout"].sum() / g["amount"].sum(),
            }), include_groups=False
        ).reset_index()
        by_type = s.groupby("bet_type").apply(
            lambda g: pd.Series({
                "点数": len(g), "的中": int((g["payout"] > 0).sum()),
                "投資額": int(g["amount"].sum()),
                "払戻額": int(g["payout"].sum()),
                "回収率": g["payout"].sum() / g["amount"].sum(),
            }), include_groups=False
        ).reset_index().rename(columns={"bet_type": "券種"})
    return {"total": total, "by_month": by_month, "by_type": by_type}
