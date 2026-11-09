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
    "finish", "time_sec", "agari",
]


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

            for r in races:
                r["entries"] = by_race.get(r["race_id"], [])
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
            })
            if (i + 1) % 50 == 0 or i + 1 == len(days):
                progress(f"書き出し {i + 1}/{len(days)}日")

        span = _rows(conn, f"SELECT MIN(date) a, MAX(date) b FROM site_races {where}")[0]
        valid = _rows(
            conn,
            f"SELECT MIN(date) d FROM site_races WHERE in_sample=0"
            + (" AND is_nar=0" if jra_only else ""),
        )[0]
        (out_dir / "data" / "index.json").write_text(
            json.dumps({
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
