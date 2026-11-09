"""これからのレース (今週末など) を予想してサイトに載せる。

終わったレースは web.build が results から作るが、こちらは netkeiba の
出馬表から作る。出力先は同じ site_races / site_preds なので、
web.export はどちらも区別せず書き出せる。

    .venv\\Scripts\\python -m web.upcoming 2026-11-14
    .venv\\Scripts\\python -m web.upcoming --next     # 直近の開催日を探す

web.build は site_races を作り直すので、必ず build のあとに実行する。
"""
import argparse
import datetime as dt
import sqlite3

import joblib
import numpy as np
import pandas as pd

from keiba.ev import predict_fukusho_odds, fukusho_ev
from keiba.features import add_market_features, load_horses, prepare_history
from keiba.predict import build_features_for_entries, fetch_shutuba
from keiba.racelist import find_jra_races
from keiba.scraper import DB_PATH, JRA_PLACES
from keiba.train import MODEL_PATH
from web.build import MARKS, PRED_COLS, race_row

# JRA は1日に最大3場。探索は1場あたり十数秒かかるので、
# 「過去の同じ月によく開催していた場」から順に当たる
# (11月なら東京・京都・福島、というような季節性がある)。
MAX_VENUES = 3


def likely_places(conn, date):
    rows = conn.execute(
        "SELECT substr(race_id,5,2) AS p, COUNT(DISTINCT date) AS n FROM results "
        "WHERE is_nar=0 AND substr(date,6,2)=? GROUP BY p ORDER BY n DESC",
        (f"{date.month:02d}",),
    ).fetchall()
    ranked = [p for p, _ in rows if p in JRA_PLACES]
    return ranked + [p for p in JRA_PLACES if p not in ranked]


def discover(date, db_path=DB_PATH, progress=print):
    """指定日の中央の race_id を全部探す。"""
    conn = sqlite3.connect(db_path)
    try:
        places = likely_places(conn, date)
    finally:
        conn.close()

    race_ids, hits = [], 0
    for pcode in places:
        progress(f"{JRA_PLACES[pcode]} を探索中...")
        ids = find_jra_races(date, pcode)
        if ids:
            progress(f"  {JRA_PLACES[pcode]}: {len(ids)}レース")
            race_ids += ids
            hits += 1
            if hits >= MAX_VENUES:
                break  # 1日3場までなので、見つかったら打ち切る
    return race_ids


def _calibrate(bundle, key, prob):
    ir = bundle.get("calibrators", {}).get(key)
    return np.clip(ir.predict(prob), 0.0, 1.0) if ir is not None else prob


def predict_one(race_id, ctx, as_of):
    """1レースを予想して (info, 1頭1行のDataFrame) を返す。

    オッズが出ていれば市場モデル、出ていなければ能力モデルを使う。
    """
    bundle, hist, horses = ctx["bundle"], ctx["hist"], ctx["horses"]
    info, entries = fetch_shutuba(race_id)
    if entries is None or entries.empty:
        return None, None

    entries = entries.copy()
    for c in ("waku", "horse_no", "age", "kinryo", "weight", "weight_diff", "odds"):
        entries[c] = (pd.to_numeric(entries[c], errors="coerce")
                      if c in entries.columns else np.nan)

    X = build_features_for_entries(info, entries, hist, horses, as_of)

    # オッズが出ていれば市場モデル (的中率が高い)、出ていなければ能力モデル。
    # 週の前半や、終わったレースの出馬表ページではオッズが載っていない。
    has_odds = entries["odds"].notna().sum() >= max(3, len(entries) // 2)
    if has_odds:
        Xm = add_market_features(X, entries["odds"].values, [race_id] * len(entries))
        p_top3 = _calibrate(bundle, "market_top3",
                            bundle["model_market_top3"].predict_proba(Xm)[:, 1])
        p_win_raw = _calibrate(bundle, "market_win",
                               bundle["model_market_win"].predict_proba(Xm)[:, 1])
    else:
        p_top3 = _calibrate(bundle, "top3",
                            bundle["model_top3"].predict_proba(X)[:, 1])
        p_win_raw = _calibrate(bundle, "win",
                               bundle["model_win"].predict_proba(X)[:, 1])
    p_top3 = np.clip(p_top3, 0.01, 0.97)

    out = pd.DataFrame({
        "race_id": race_id,
        "horse_no": entries["horse_no"].astype("Int64"),
        "waku": entries["waku"].astype("Int64"),
        "horse_name": entries["horse_name"],
        "sex": entries.get("sex"),
        "age": entries["age"].astype("Int64"),
        "kinryo": entries["kinryo"],
        "jockey_name": entries.get("jockey_name"),
        "trainer_name": entries.get("trainer_name"),
        "weight": entries["weight"],
        "weight_diff": entries["weight_diff"],
        "odds": entries["odds"],
        "p_top3": p_top3,
        "p_win": p_win_raw / p_win_raw.sum(),
        "field_size": len(entries),
        "finish": None, "time_sec": None, "agari": None, "corner1": None,
    })
    out["fair_odds"] = 1.0 / out["p_win"].replace(0, np.nan)
    if has_odds:
        # 人気は確定前なのでオッズ順から付ける
        out["popularity"] = out["odds"].rank(method="min").astype("Int64")
        out["fuku_odds"] = predict_fukusho_odds(bundle.get("fuku_odds_model"), out)
        out["ev_fuku"] = fukusho_ev(out["p_top3"], out["fuku_odds"])
    else:
        # 複勝オッズの予測は単勝オッズと人気が要るので、出るまで保留
        out["popularity"] = pd.NA
        out["fuku_odds"] = np.nan
        out["ev_fuku"] = np.nan

    # 血統は手元の馬マスタから引く (未登録なら空欄)
    ped = ctx["ped"]
    ids = entries["horse_id"]
    out["sire"] = ids.map(ped["sire_name"]) if not ped.empty else None
    out["dam_sire"] = ids.map(ped["dam_sire_name"]) if not ped.empty else None

    out = out.sort_values("p_win", ascending=False).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    out["mark"] = [MARKS[r - 1] if r <= len(MARKS) else "" for r in out["rank"]]
    return info, out


def run(date, db_path=DB_PATH, model_path=MODEL_PATH, race_ids=None, progress=print):
    race_ids = race_ids or discover(date, db_path, progress)
    if not race_ids:
        progress(f"{date} に中央の開催は見つかりませんでした。")
        return {"races": 0}

    progress(f"{len(race_ids)}レースを予想します。モデルと過去成績を読み込み中...")
    ctx = {"bundle": joblib.load(model_path)}
    ctx["hist"], ctx["horses"] = prepare_history(db_path)
    ped = load_horses(db_path).drop_duplicates("horse_id").set_index("horse_id")
    ctx["ped"] = ped

    as_of = pd.Timestamp(date)
    race_rows, pred_rows, skipped = [], [], []
    for i, race_id in enumerate(race_ids, 1):
        try:
            info, out = predict_one(race_id, ctx, as_of)
        except Exception as e:
            skipped.append(f"{race_id}: {e}")
            continue
        if out is None:
            skipped.append(f"{race_id}: 出馬表が空")
            continue
        info = {**info, "date": str(date), "weather": None,
                "post_time": None, "grade": None}
        row = race_row(race_id, out, info)
        if out["odds"].isna().all():
            row["reason"] += " ／ オッズ未発表のため能力モデルのみ"
        race_rows.append(row)
        pred_rows.append(out)
        progress(f"[{i}/{len(race_ids)}] {info['place']}{info['race_no']}R "
                 f"◎{out.iloc[0]['horse_name']}")

    if not race_rows:
        progress("予想できたレースがありませんでした。")
        for s in skipped[:5]:
            progress(f"  {s}")
        return {"races": 0}

    races = pd.DataFrame(race_rows)
    preds = pd.concat(pred_rows, ignore_index=True)
    for c in PRED_COLS:
        if c not in preds.columns:
            preds[c] = None
    preds = preds[PRED_COLS].replace({np.nan: None})

    conn = sqlite3.connect(db_path, timeout=120)
    try:
        conn.execute("DELETE FROM site_races WHERE date=?", (str(date),))
        conn.execute(
            "DELETE FROM site_preds WHERE race_id IN "
            "(SELECT race_id FROM site_races WHERE date=?)", (str(date),))
        races.to_sql("site_races", conn, if_exists="append", index=False)
        preds.to_sql("site_preds", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()

    progress(f"{len(races)}レースを site_races に追加しました"
             + (f" (見送り {len(skipped)}件)" if skipped else ""))
    return {"races": len(races), "skipped": skipped}


def next_race_day(start=None, ahead=14, db_path=DB_PATH, progress=print):
    """出馬表が出ている直近の開催日を探す (土日を優先)。"""
    start = start or dt.date.today()
    candidates = [start + dt.timedelta(days=d) for d in range(ahead + 1)]
    candidates.sort(key=lambda d: (d.weekday() < 5, d))  # 土日を先に
    for date in candidates:
        progress(f"{date} を確認中...")
        ids = discover(date, db_path, progress=lambda m: None)
        if ids:
            return date, ids
    return None, []


def main():
    ap = argparse.ArgumentParser(description="これからのレースを予想する")
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD")
    ap.add_argument("--next", action="store_true", help="直近の開催日を自動で探す")
    args = ap.parse_args()

    if args.next or not args.date:
        date, ids = next_race_day()
        if date is None:
            print("出馬表が出ている開催日が見つかりませんでした。")
            return
        print(f"直近の開催日: {date}")
        run(date, race_ids=ids)
    else:
        run(dt.date.fromisoformat(args.date))


if __name__ == "__main__":
    main()
