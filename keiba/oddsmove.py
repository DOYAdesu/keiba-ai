"""オッズの動き (前日最終 → 発走40分前) と、その後の成績の関係を調べてサイトに載せる。

  python -m keiba.oddsmove      # site_meta['odds_stats'] を作り直す

比べ方: 40分前の人気が同じ馬の3着内率を「期待」とし、動き別の実際の3着内率と比べる。
単勝回収率は確定オッズで払い戻した場合 (40分前に買っても払戻は確定オッズで決まる)。
"""
import json
import sqlite3

import numpy as np
import pandas as pd

from .scraper import DB_PATH

BUCKETS = [
    (0, 0.67, "大きく売れた（3分の2以下）"),
    (0.67, 0.8, "売れた（2割以上下がった）"),
    (0.8, 1.25, "ほぼ変わらず"),
    (1.25, 1.5, "人気が落ちた（2.5割以上上がった）"),
    (1.5, 1e9, "大きく人気が落ちた（1.5倍以上）"),
]


def _expand(conn, tag):
    rows = conn.execute("SELECT race_id, win FROM odds_hist WHERE tag=? AND win IS NOT NULL",
                        (tag,)).fetchall()
    out = []
    for rid, win in rows:
        for i, v in enumerate(win.split(",")):
            if v:
                out.append((rid, i + 1, float(v)))
    return pd.DataFrame(out, columns=["race_id", "horse_no", tag])


def odds_at(meta, tag="t40", db_path=DB_PATH):
    """meta (race_id, horse_no) に合わせた時系列オッズ (既定は発走40分前)。無い馬は NaN。"""
    conn = sqlite3.connect(db_path)
    try:
        try:
            t = _expand(conn, tag)
        except sqlite3.OperationalError:
            return pd.Series(np.nan, index=meta.index)
    finally:
        conn.close()
    if t.empty:
        return pd.Series(np.nan, index=meta.index)
    m = t.set_index(["race_id", "horse_no"])[tag]
    key = pd.MultiIndex.from_arrays([meta["race_id"].astype(str),
                                     pd.to_numeric(meta["horse_no"], errors="coerce").astype("Int64")])
    return pd.Series(m.reindex(key).to_numpy(), index=meta.index, dtype=float)


def model_odds(meta, db_path=DB_PATH):
    """市場モデルに入れるオッズ: 発走40分前 (実際に予想を出す時点)。無ければ確定オッズ。

    確定オッズで学習・答え合わせをすると、実際より良く見える (scripts/refine_live.py B)。
    """
    o40 = odds_at(meta, "t40", db_path)
    return o40.fillna(pd.to_numeric(meta["odds"], errors="coerce"))


def frame(conn):
    pre, t40 = _expand(conn, "pre"), _expand(conn, "t40")
    res = pd.read_sql("SELECT race_id, horse_no, finish, odds, date FROM results "
                      "WHERE is_nar=0 AND finish IS NOT NULL", conn)
    d = res.merge(pre, on=["race_id", "horse_no"]).merge(t40, on=["race_id", "horse_no"])
    d["move"] = d["t40"] / d["pre"]
    d["pop40"] = d.groupby("race_id")["t40"].rank(method="min").clip(upper=12)
    d["top3"] = (d["finish"] <= 3).astype(float)
    d["win"] = (d["finish"] == 1).astype(float)
    return d


def stats(d):
    base = d.groupby("pop40")["top3"].mean()
    d = d.assign(expected=d["pop40"].map(base))
    rows = []
    for lo, hi, label in BUCKETS:
        g = d[(d["move"] > lo) & (d["move"] <= hi)]
        if len(g) < 100:
            continue
        rows.append({"label": label, "n": int(len(g)),
                     "top3": round(float(g["top3"].mean()), 4),
                     "expected": round(float(g["expected"].mean()), 4),
                     "roi_win": round(float((g["win"] * g["odds"]).sum() / len(g)), 3)})
    return rows


def build(db_path=DB_PATH, log=print):
    conn = sqlite3.connect(db_path)
    try:
        d = frame(conn)
        if d.empty:
            log("オッズの時系列がまだありません")
            return None
        rows = stats(d)
        diffs = [abs(r["top3"] - r["expected"]) for r in rows]
        if max(diffs) < 0.02:
            note = ("40分前の人気が同じ馬どうしで比べると、前日からの動きによる3着内率の差は"
                    "1〜2ポイント以内でした。動きはほぼ40分前のオッズに織り込まれているので、"
                    "矢印は「何が売れているか」を見る目安にしてください。"
                    "単勝回収率の差は主に人気の差（人気馬ほど回収率が高い）によるものです。")
        else:
            note = ("差は40分前の人気が同じ馬どうしで比べたものです。"
                    "単勝回収率の差には人気の差（人気馬ほど回収率が高い）も含まれます。")
        out = {"rows": rows, "races": int(d["race_id"].nunique()),
               "from": str(d["date"].min())[:10], "note": note}
        conn.execute("CREATE TABLE IF NOT EXISTS site_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO site_meta VALUES ('odds_stats', ?)",
                     (json.dumps(out, ensure_ascii=False),))
        conn.commit()
        for r in rows:
            log(f"{r['label']}: {r['n']}頭 3着内 {r['top3']:.1%} (同じ人気 {r['expected']:.1%}) "
                f"単勝回収率 {r['roi_win']:.0%}")
        return out
    finally:
        conn.close()


if __name__ == "__main__":
    build()
