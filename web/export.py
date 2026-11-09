"""サイトを静的ファイルに書き出す (Cloudflare Pages 配信用)。

事前計算済みの site_races / site_preds を「1開催日 = 1 JSON」にまとめる。
1日ぶんを取れば トップ・予想・結果・レース詳細 が全部描けるので、
画面遷移のたびに取りに行かなくて済む。

    .venv\\Scripts\\python -m web.export          # dist/ に書き出す
    .venv\\Scripts\\python -m web.export --all    # 地方競馬も含める
"""
import argparse
import datetime as dt
import json
import shutil
import sqlite3
from pathlib import Path

from keiba.scraper import DB_PATH, PROJECT_ROOT

DIST = PROJECT_ROOT / "dist"
STATIC = Path(__file__).parent / "static"

# 1頭ぶんで画面が使う列だけ (ファイルを無駄に太らせない)
ENTRY_COLS = [
    "rank", "mark", "horse_no", "waku", "horse_name", "sire", "dam_sire",
    "sex", "age", "kinryo", "jockey_name", "trainer_name",
    "weight", "weight_diff", "odds", "popularity",
    "p_win", "p_top3", "fair_odds", "fuku_odds", "ev_fuku",
    "finish", "time_sec", "agari", "ability_idx", "style",
] + ["c_spd", "c_agari", "c_recent", "c_career", "c_dist", "c_course", "c_jockey", "c_trainer", "c_sire", "c_going"]

# 血統タブ: 種牡馬ごとの産駒成績 (この出走数未満は数字がぶれるので載せない)
SIRE_MIN_STARTS = 40


def sire_stats(conn):
    """種牡馬ごとの産駒成績 (芝ダ・距離帯別の複勝率つき)。"""
    import pandas as pd

    from keiba.features import normalize_bloodline

    conn.row_factory = None  # _rows が Row に変えているので戻す
    df = pd.read_sql(
        "SELECT r.surface, r.distance, r.finish, r.odds, h.sire_name "
        "FROM results r JOIN horses h ON h.horse_id = r.horse_id "
        "WHERE r.is_nar = 0 AND r.finish IS NOT NULL AND h.sire_name IS NOT NULL",
        conn,
    )
    if df.empty:
        return []
    df["sire"] = df["sire_name"].map(normalize_bloodline)
    df = df.dropna(subset=["sire"])
    df["top3"] = df["finish"] <= 3
    df["win"] = df["finish"] == 1
    df["band"] = pd.cut(df["distance"], [0, 1400, 1800, 2200, 9999],
                        labels=["短", "マイル", "中", "長"])

    def rate(s):
        return round(float(s.mean()), 3) if len(s) >= 10 else None

    out = []
    for sire, g in df.groupby("sire"):
        if len(g) < SIRE_MIN_STARTS:
            continue
        wins = g[g["win"] & g["odds"].notna()]
        out.append({
            "sire": sire,
            "n": int(len(g)),
            "win": round(float(g["win"].mean()), 3),
            "top3": round(float(g["top3"].mean()), 3),
            "turf": rate(g.loc[g["surface"] == "芝", "top3"]),
            "dirt": rate(g.loc[g["surface"] == "ダ", "top3"]),
            "short": rate(g.loc[g["band"] == "短", "top3"]),
            "mile": rate(g.loc[g["band"] == "マイル", "top3"]),
            "mid": rate(g.loc[g["band"] == "中", "top3"]),
            "long": rate(g.loc[g["band"] == "長", "top3"]),
            # 単勝を全部100円で買った場合の回収率
            "roi": round(float(wins["odds"].sum() / len(g)), 3),
        })
    out.sort(key=lambda s: -s["n"])
    return out


def _rows(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params)]


def _round(value, digits):
    return None if value is None else round(value, digits)


def _slim(entry):
    """小数をそのまま出すとファイルが膨らむので桁を落とす。"""
    e = {k: entry.get(k) for k in ENTRY_COLS}
    for k, d in (("p_win", 4), ("p_top3", 4), ("fair_odds", 1),
                 ("fuku_odds", 2), ("ev_fuku", 3)):
        e[k] = _round(e[k], d)
    return {k: v for k, v in e.items() if v is not None}


def day_summary(races):
    """結果タブの集計 (本命と推奨の的中率・回収率)。"""
    done = [r for r in races if r["top_finish"] is not None]
    rec = [r for r in done if r["is_rec"]]

    def rate(rows, cond):
        return round(sum(1 for r in rows if cond(r)) / len(rows), 4) if rows else None

    def roi(rows, payout):
        if not rows:
            return None
        return round(sum(payout(r) or 0 for r in rows) / (len(rows) * 100), 4)

    def tansho(r):
        return r["top_odds"] * 100 if r["top_finish"] == 1 and r["top_odds"] else 0

    return {
        "n_races": len(races),
        "honmei_win_rate": rate(done, lambda r: r["top_finish"] == 1),
        "honmei_top3_rate": rate(done, lambda r: r["top_finish"] <= 3),
        "honmei_tansho_roi": roi(done, tansho),
        "n_rec": len(rec),
        "rec_win_rate": rate(rec, lambda r: r["top_finish"] == 1),
        "rec_tansho_roi": roi(rec, tansho),
        "rec_top3_rate": rate(rec, lambda r: r["top_finish"] <= 3),
        "rec_fukusho_roi": roi(rec, lambda r: r["top_fuku_pay"]),
    }


def export(db_path=DB_PATH, out_dir=DIST, jra_only=True, progress=print):
    conn = sqlite3.connect(db_path)
    try:
        if not _rows(conn, "SELECT name FROM sqlite_master "
                           "WHERE type='table' AND name='site_races'"):
            raise RuntimeError("先に `python -m web.build` を実行してください。")

        where = "WHERE is_nar=0" if jra_only else ""
        days = [r["date"] for r in _rows(
            conn, f"SELECT DISTINCT date FROM site_races {where} ORDER BY date DESC"
        )]
        if not days:
            raise RuntimeError("書き出す開催日がありません。")

        out_dir = Path(out_dir)
        if out_dir.exists():
            shutil.rmtree(out_dir)
        (out_dir / "data" / "day").mkdir(parents=True)

        # 画面本体 (HTML/JS/CSS) をそのまま置く
        for src in STATIC.iterdir():
            if src.is_file():
                shutil.copy2(src, out_dir / src.name)

        index_days = []
        for i, date in enumerate(days):
            races = _rows(
                conn,
                f"SELECT * FROM site_races WHERE date=? "
                f"{'AND is_nar=0' if jra_only else ''} ORDER BY place, race_no",
                (date,),
            )
            entries = _rows(
                conn,
                "SELECT p.* FROM site_preds p JOIN site_races r "
                "ON r.race_id=p.race_id WHERE r.date=?"
                + (" AND r.is_nar=0" if jra_only else "")
                + " ORDER BY p.race_id, p.horse_no",
                (date,),
            )
            by_race = {}
            for e in entries:
                by_race.setdefault(e["race_id"], []).append(_slim(e))

            # 各レースの払戻 (レースページで買い目の的中を表示する)
            pays = {}
            for pr in _rows(
                conn,
                "SELECT p.race_id, p.bet_type, p.combo, p.pay FROM payouts p "
                "JOIN site_races r ON r.race_id=p.race_id WHERE r.date=?",
                (date,),
            ):
                pays.setdefault(pr["race_id"], {}).setdefault(
                    pr["bet_type"], []).append([pr["combo"], pr["pay"]])

            for r in races:
                r["entries"] = by_race.get(r["race_id"], [])
                if r["race_id"] in pays:
                    r["payouts"] = pays[r["race_id"]]
                for k, d in (("top_p_win", 4), ("top_p_top3", 4),
                             ("top_ev", 3), ("confidence", 3),
                             ("p_hit_100k", 4), ("p_hit_1m", 4),
                             ("max_payout", 0)):
                    r[k] = _round(r.get(k), d)

            payload = {
                "date": date,
                "in_sample": races[0]["in_sample"],
                # days は新しい順なので、次の日付は添字が小さい方
                "prev": days[i + 1] if i + 1 < len(days) else None,
                "next": days[i - 1] if i > 0 else None,
                "summary": day_summary(races),
                "races": races,
            }
            path = out_dir / "data" / "day" / f"{date}.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            index_days.append({
                "date": date,
                "n_races": len(races),
                "n_rec": sum(r["is_rec"] for r in races),
                "places": sorted({r["place"] for r in races}),
                "in_sample": races[0]["in_sample"],
                # 出走前の日は「登録」(特別登録のみ) / 「出馬表」、終わった日は None
                "stage": next((r.get("stage") for r in races if r.get("stage")), None),
                "graded": [f"{r['place']}{r['race_no']}R {r['race_name']}"
                           for r in races if r.get("grade") in ("G1", "G2", "G3")],
                "n_million": sum(1 for r in races if (r.get("million_n") or 0) > 0),
            })
            if (i + 1) % 50 == 0 or i + 1 == len(days):
                progress(f"書き出し {i + 1}/{len(days)}日")

        sires = sire_stats(conn)
        (out_dir / "data" / "sires.json").write_text(
            json.dumps(sires, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        progress(f"種牡馬 {len(sires)}頭の産駒成績を書き出しました")

        span = _rows(conn, f"SELECT MIN(date) a, MAX(date) b FROM site_races {where}")[0]
        valid = _rows(
            conn,
            f"SELECT MIN(date) d FROM site_races WHERE in_sample=0"
            + (" AND is_nar=0" if jra_only else ""),
        )[0]
        report = _rows(conn, "SELECT value FROM site_meta WHERE key='index_report'")
        index_report = json.loads(report[0]["value"]) if report else None
        urow = _rows(conn, "SELECT value FROM site_meta WHERE key='upset_report'")
        upset_report = json.loads(urow[0]["value"]) if urow else None
        mrow = _rows(conn, "SELECT value FROM site_meta WHERE key='million_rules'")
        million_meta = json.loads(mrow[0]["value"]) if mrow else None
        if million_meta:
            # 法則に当てはまったレースの一覧 (直近1年と、これからのレース)
            hits = _rows(
                conn,
                "SELECT date, race_id, place, race_no, race_name, field_size, million_n, "
                "million_rules, trifecta_pay, stage FROM site_races "
                "WHERE million_n > 0 AND is_nar = 0 AND date >= date('now', '-365 day') "
                "ORDER BY date DESC, million_n DESC, place, race_no")
            million_meta["races"] = hits
            (out_dir / "data" / "million.json").write_text(
                json.dumps(million_meta, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8")
            progress(f"100万馬券の法則 {len(million_meta['rules'])}件 / 当てはまり {len(hits)}レース")
        (out_dir / "data" / "index.json").write_text(
            json.dumps({
                "index_report": index_report,
                "upset_report": upset_report,
                "generated": dt.datetime.now().isoformat(timespec="seconds"),
                "from": span["a"], "to": span["b"],
                "valid_from": valid["d"],
                "jra_only": jra_only,
                "days": index_days,
            }, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    finally:
        conn.close()

    files = list(out_dir.rglob("*"))
    size = sum(f.stat().st_size for f in files if f.is_file())
    progress(
        f"{out_dir} に書き出しました: "
        f"{sum(1 for f in files if f.is_file()):,}ファイル / {size / 1024 / 1024:.1f}MB"
    )
    return {"days": len(days), "dir": str(out_dir)}


def main():
    ap = argparse.ArgumentParser(description="サイトを静的ファイルに書き出す")
    ap.add_argument("--all", action="store_true", help="地方競馬も含める")
    ap.add_argument("--out", default=str(DIST))
    args = ap.parse_args()
    export(out_dir=args.out, jra_only=not args.all)


if __name__ == "__main__":
    main()
