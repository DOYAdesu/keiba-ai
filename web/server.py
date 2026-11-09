"""閲覧用サイトの API + 静的配信。

事前計算済みの site_races / site_preds を読むだけなので、
リクエストごとにモデルを動かさない。

    .venv\\Scripts\\python -m web.server
    → http://127.0.0.1:8000
"""
import sqlite3
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from keiba.scraper import DB_PATH

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="競馬予想AI")


def q(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def q1(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def _ensure_built():
    if not q1("SELECT name FROM sqlite_master WHERE type='table' AND name='site_races'"):
        raise HTTPException(
            503, "事前計算がまだです。`python -m web.build` を実行してください。"
        )


def _rate(rows, cond):
    """rows のうち cond を満たす割合。母数0なら None。"""
    n = len(rows)
    return round(sum(1 for r in rows if cond(r)) / n, 4) if n else None


def _roi(rows, stake_rows, payout):
    """払戻合計 ÷ 賭け金合計。stake_rows は買った行、payout は払戻金を返す関数。"""
    n = len(stake_rows)
    if not n:
        return None
    return round(sum(payout(r) or 0 for r in stake_rows) / (n * 100), 4)


@app.get("/api/meta")
def meta():
    """データ期間と、学習に使っていない検証期間の開始日。"""
    _ensure_built()
    row = q1("SELECT MIN(date) AS d_from, MAX(date) AS d_to, COUNT(*) AS n_races "
             "FROM site_races")
    split = q1("SELECT MIN(date) AS d FROM site_races WHERE in_sample=0")
    return {**row, "valid_from": (split or {}).get("d")}


@app.get("/api/days")
def days(limit: int = 60, jra_only: bool = True):
    """開催日の一覧（新しい順）。"""
    _ensure_built()
    where = "WHERE is_nar=0" if jra_only else ""
    return q(
        f"SELECT date, COUNT(*) AS n_races, SUM(is_rec) AS n_rec, "
        f"       MIN(in_sample) AS in_sample, "
        f"       GROUP_CONCAT(DISTINCT place) AS places "
        f"FROM site_races {where} GROUP BY date ORDER BY date DESC LIMIT ?",
        (limit,),
    )


@app.get("/api/day/{date}")
def day(date: str, jra_only: bool = True):
    """1開催日ぶんの推奨レースと、場ごとのレース一覧。"""
    _ensure_built()
    where = "AND is_nar=0" if jra_only else ""
    races = q(
        f"SELECT * FROM site_races WHERE date=? {where} "
        f"ORDER BY place, race_no", (date,)
    )
    if not races:
        raise HTTPException(404, f"{date} のデータがありません。")

    venues = {}
    for r in races:
        venues.setdefault(r["place"], []).append(r)

    recommends = sorted(
        [r for r in races if r["is_rec"]],
        key=lambda r: (-r["is_star"], -r["confidence"]),
    )
    nav = q1(
        f"SELECT MAX(date) AS prev FROM site_races WHERE date<? {where}", (date,)
    ) or {}
    nav_next = q1(
        f"SELECT MIN(date) AS next FROM site_races WHERE date>? {where}", (date,)
    ) or {}
    return {
        "date": date,
        "in_sample": races[0]["in_sample"],
        "prev": nav.get("prev"),
        "next": nav_next.get("next"),
        "recommends": recommends,
        "venues": [{"place": p, "races": rs} for p, rs in venues.items()],
    }


@app.get("/api/results/{date}")
def results(date: str, jra_only: bool = True):
    """その日の成績（本命・推奨の的中率と回収率）と、推奨レースの答え合わせ。"""
    _ensure_built()
    where = "AND is_nar=0" if jra_only else ""
    races = q(f"SELECT * FROM site_races WHERE date=? {where}", (date,))
    if not races:
        raise HTTPException(404, f"{date} のデータがありません。")

    done = [r for r in races if r["top_finish"] is not None]
    rec = [r for r in done if r["is_rec"]]

    def tansho(r):
        return r["top_odds"] * 100 if r["top_finish"] == 1 and r["top_odds"] else 0

    summary = {
        "n_races": len(races),
        "honmei_win_rate": _rate(done, lambda r: r["top_finish"] == 1),
        "honmei_top3_rate": _rate(done, lambda r: r["top_finish"] <= 3),
        "honmei_tansho_roi": _roi(done, done, tansho),
        "n_rec": len(rec),
        "rec_win_rate": _rate(rec, lambda r: r["top_finish"] == 1),
        "rec_tansho_roi": _roi(rec, rec, tansho),
        "rec_top3_rate": _rate(rec, lambda r: r["top_finish"] <= 3),
        "rec_fukusho_roi": _roi(rec, rec, lambda r: r["top_fuku_pay"]),
    }
    prev = q1(f"SELECT MAX(date) AS d FROM site_races WHERE date<? {where}", (date,))
    nxt = q1(f"SELECT MIN(date) AS d FROM site_races WHERE date>? {where}", (date,))
    return {
        "date": date,
        "in_sample": races[0]["in_sample"],
        "prev": (prev or {}).get("d"),
        "next": (nxt or {}).get("d"),
        "summary": summary,
        "recommends": sorted(rec, key=lambda r: (r["place"], r["race_no"])),
    }


@app.get("/api/race/{race_id}")
def race(race_id: str):
    """レース詳細（出馬表 + AI予想）。"""
    _ensure_built()
    info = q1("SELECT * FROM site_races WHERE race_id=?", (race_id,))
    if not info:
        raise HTTPException(404, f"{race_id} のデータがありません。")
    entries = q(
        "SELECT * FROM site_preds WHERE race_id=? ORDER BY horse_no", (race_id,)
    )
    same_day = q(
        "SELECT race_id, race_no, is_rec FROM site_races "
        "WHERE date=? AND place=? ORDER BY race_no",
        (info["date"], info["place"]),
    )
    others = q(
        "SELECT race_id, place, race_no FROM site_races "
        "WHERE date=? AND place<>? AND race_no=? ORDER BY place",
        (info["date"], info["place"], info["race_no"]),
    )
    return {"info": info, "entries": entries, "same_day": same_day,
            "other_venues": others}


@app.middleware("http")
async def no_cache(request, call_next):
    """JS/CSS を直したのに古いままになるのを防ぐ (ローカル専用なので常に無効化)。"""
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


def main():
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
