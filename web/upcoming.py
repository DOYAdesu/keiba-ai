"""これからのレース (今週末など) を予想してサイトに載せる。

終わったレースは web.build が results から作るが、こちらは netkeiba の
出馬表から作る。出力先は同じ site_races / site_preds なので、
web.export はどちらも区別せず書き出せる。

    .venv\\Scripts\\python -m web.upcoming 2026-11-14
    .venv\\Scripts\\python -m web.upcoming --next     # 直近の開催日を探す
    .venv\\Scripts\\python -m web.upcoming --week     # 今日から7日分 (土日両方)

出馬表が出る前 (日〜水) でも、JRA-VAN の特別登録 (TK) があれば
特別レースだけ「登録段階」として予想する。

web.build は site_races を作り直すので、必ず build のあとに実行する。
"""
import argparse
import datetime as dt
import sqlite3

import joblib
import numpy as np
import pandas as pd

from keiba.ev import predict_fukusho_odds, fukusho_ev
from keiba.features import (
    add_market_features, load_horses, normalize_bloodline, prepare_history,
)
from keiba.predict import build_features_for_entries, fetch_shutuba
from keiba.racelist import find_jra_races
from keiba.scraper import DB_PATH, JRA_PLACES
from keiba.train import MODEL_PATH
from web import insight, million
from web.build import MARKS, PRED_COLS, enrich, race_row

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


def known_races(date, db_path=DB_PATH):
    """JRA-VAN で出馬表まで取り込み済みのレース (race_id → 発走時刻)。無ければ空。"""
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT r.race_id, r.post_time FROM races r WHERE r.date=? "
                "AND CAST(substr(r.race_id,5,2) AS INT) BETWEEN 1 AND 10 "
                "AND EXISTS (SELECT 1 FROM entries e WHERE e.race_id=r.race_id) "
                "ORDER BY r.race_id", (str(date),)).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {rid: pt for rid, pt in rows}


def toku_races(date, db_path=DB_PATH):
    """特別登録だけあって出馬表がまだのレース。"""
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT t.race_id FROM tokubetsu t JOIN races r "
                "ON r.race_id=t.race_id WHERE r.date=? AND NOT EXISTS "
                "(SELECT 1 FROM entries e WHERE e.race_id=t.race_id) "
                "ORDER BY t.race_id", (str(date),)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r[0] for r in rows]


def race_meta(race_id, db_path=DB_PATH):
    """races テーブルのレース情報 (発走時刻・グレード込み)。"""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT place, race_no, race_name, surface, distance, going, "
                "post_time, grade FROM races WHERE race_id=?", (race_id,)).fetchone()
    except sqlite3.OperationalError:
        return {}
    return dict(row) if row else {}


def trainers_of(race_id, db_path=DB_PATH):
    """JRA-VAN の出馬表から 馬番→(調教師ID, 調教師名)。netkeiba の出馬表には無いため。"""
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT horse_no, trainer_id, trainer_name FROM entries WHERE race_id=?",
                (race_id,)).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {int(n): (tid, tname) for n, tid, tname in rows if n is not None}


def discover(date, db_path=DB_PATH, progress=print):
    """指定日の中央の race_id を全部探す。

    JRA-VAN で出馬表を取り込んでいればそれを使う (一瞬で終わる)。
    無ければ netkeiba の出馬表を場ごとに探索する (1場あたり十数秒)。
    """
    known = known_races(date, db_path)
    if known:
        progress(f"JRA-VAN の出馬表から {len(known)}レースを見つけました")
        return list(known)

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
    try:
        info, entries = fetch_shutuba(race_id)
    except Exception:
        info, entries = None, None
    if entries is None or entries.empty:
        # netkeiba で取れなければ JRA-VAN の出馬表を使う (オッズは無い)
        info, entries = jv_entries(race_id, ctx.get("db_path", DB_PATH))
        if entries is None or entries.empty:
            return None, None
    return info, score(race_id, info, entries, ctx, as_of)


def jv_entries(race_id, db_path=DB_PATH):
    """JRA-VAN で取り込んだ出馬表 (entries テーブル) から作る。"""
    meta = race_meta(race_id, db_path)
    try:
        with sqlite3.connect(db_path) as conn:
            df = pd.read_sql(
                "SELECT horse_no, waku, horse_id, horse_name, sex, age, kinryo, "
                "jockey_id, jockey_name, trainer_id, trainer_name, weight, "
                "weight_diff, odds FROM entries WHERE race_id=? ORDER BY horse_no",
                conn, params=(race_id,))
    except Exception:
        return None, None
    if df.empty or not meta:
        return None, None
    info = {"race_id": race_id, "is_nar": 0, **meta}
    return info, df


def toku_entries(race_id, date, db_path=DB_PATH, hist=None):
    """特別登録 (TK) から「登録馬の出馬表」を作る。枠・馬番・騎手は未確定。

    モデルは騎手・馬体重が空の行を学習していないので、空のまま通すと
    勝率が不安定になる。そこで前走の騎手・馬体重を仮に入れる
    (乗り替わりもあるが、多くは継続騎乗)。枠・馬番は score で中央に寄せる。
    """
    meta = race_meta(race_id, db_path)
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql(
            "SELECT num AS horse_no, horse_id, horse_name, sex, trainer_id, "
            "trainer_name, kinryo FROM tokubetsu WHERE race_id=? ORDER BY num",
            conn, params=(race_id,))
    if df.empty or not meta:
        return None, None
    # 年齢は血統登録番号の頭4桁 (生年) から出す
    birth = pd.to_numeric(df["horse_id"].str[:4], errors="coerce")
    df["age"] = date.year - birth
    for c in ("waku", "weight", "weight_diff", "odds"):
        df[c] = np.nan
    df["jockey_id"] = None
    df["jockey_name"] = None
    if hist is not None:
        as_of = pd.Timestamp(date)
        for i, hid in enumerate(df["horse_id"]):
            h = hist.horse(hid)
            if h is None or h.empty:
                continue
            h = h[h["date"] < as_of].sort_values("date")
            if h.empty:
                continue
            last = h.iloc[-1]
            df.loc[df.index[i], "jockey_id"] = last.get("jockey_id")
            df.loc[df.index[i], "jockey_name"] = last.get("jockey_name")
            df.loc[df.index[i], "weight"] = last.get("weight")
            df.loc[df.index[i], "weight_diff"] = 0.0
    info = {"race_id": race_id, "is_nar": 0, "_toku": True, **meta}
    return info, df


def score(race_id, info, entries, ctx, as_of):
    """出馬表 (または登録馬) を採点して 1頭1行の DataFrame を返す。"""
    bundle, hist, horses = ctx["bundle"], ctx["hist"], ctx["horses"]
    entries = entries.copy()
    for c in ("waku", "horse_no", "age", "kinryo", "weight", "weight_diff", "odds"):
        entries[c] = (pd.to_numeric(entries[c], errors="coerce")
                      if c in entries.columns else np.nan)
    # 調教師は JRA-VAN の出馬表から補う (騎手×調教師の根拠に使う)
    tr = trainers_of(race_id, ctx.get("db_path", DB_PATH))
    if tr:
        nos = entries["horse_no"]
        entries["trainer_id"] = [tr.get(int(n), (None, None))[0] if pd.notna(n) else None
                                 for n in nos]
        entries["trainer_name"] = [tr.get(int(n), (None, None))[1] if pd.notna(n) else None
                                   for n in nos]

    if info.get("_toku"):
        # 登録段階は枠・馬番が無い。登録順を馬番として使うと内外の偏りが
        # 出るので、特徴量の上では全頭を中ほどの枠に置き、頭数はフルゲート扱い
        feat = entries.copy()
        feat["horse_no"] = 8
        feat["waku"] = 4
        X = build_features_for_entries(info, feat, hist, horses, as_of)
        X["field_size"] = min(len(entries), 18)
    else:
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
    out["horse_id"] = ids.values
    out["sire"] = ids.map(ped["sire_name"]) if not ped.empty else None
    out["dam_sire"] = ids.map(ped["dam_sire_name"]) if not ped.empty else None

    # 能力指数・脚質・騎手×調教師 (終わったレースと同じ物差しで出す)
    p_ability = _calibrate(bundle, "top3", bundle["model_top3"].predict_proba(X)[:, 1])
    out["ability_idx"] = insight.to_index(p_ability, ctx["index_table"])
    out["style_ratio"] = X["avg_corner1_ratio5"].values
    out["style"] = [insight.style_of(v) for v in out["style_ratio"]]
    hdf = hist.df
    jt = [insight.jt_rate_asof(hdf, j, t, as_of) for j, t in zip(
        entries.get("jockey_id", pd.Series([None] * len(entries))),
        entries.get("trainer_id", pd.Series([None] * len(entries))))]
    out["jt_n"] = [n for n, _ in jt]
    out["jt_rate"] = [r for _, r in jt]

    # 能力指数の内訳 (終わったレースと同じ物差し)
    raw = pd.DataFrame(index=out.index)
    for col, xcol in insight.COMPONENT_FROM_X.items():
        raw[col] = X[xcol].values
    tids = entries.get("trainer_id", pd.Series([None] * len(entries))).tolist()
    jids = entries.get("jockey_id", pd.Series([None] * len(entries))).tolist()
    sires = [normalize_bloodline(hist.pedigree_of(h)[0]) for h in entries["horse_id"]]
    extra = [insight.components_asof(ctx.get("figs", {}), h, t, info.get("going"), as_of,
                                     jockey_id=j, place_cd=race_id[4:6],
                                     distance=info.get("distance"),
                                     surface=info.get("surface"),
                                     sire_key=sk if isinstance(sk, str) else None)
             for h, t, j, sk in zip(entries["horse_id"], tids, jids, sires)]
    for k in ("c_spd", "c_agari", "c_recent", "c_career", "c_dist", "c_course",
              "c_going", "c_jockey", "c_trainer", "c_sire"):
        raw[k] = [e[k] for e in extra]
    scores = insight.component_scores(raw, ctx.get("comp_tables", {}))
    for c in scores.columns:
        out[c] = scores[c].values
    # 総合の指数 (AI7割 + 内訳3割。事前計算と同じ物差し)
    out["ability_idx"] = insight.apply_index_blend(
        out["ability_idx"].values, scores.reset_index(drop=True), ctx.get("index_blend", {}))

    out = out.sort_values(["p_win", "p_top3"], ascending=False).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    out["mark"] = [MARKS[r - 1] if r <= len(MARKS) else "" for r in out["rank"]]
    return out


def load_ctx(db_path=DB_PATH, model_path=MODEL_PATH):
    """予想に要るもの (モデル・過去成績・指数の物差しなど) をまとめて読む。"""
    ctx = {"bundle": joblib.load(model_path), "db_path": db_path}
    ctx["hist"], ctx["horses"] = prepare_history(db_path)
    ctx["ped"] = load_horses(db_path).drop_duplicates("horse_id").set_index("horse_id")
    ctx["index_table"] = insight.load_meta(db_path, "index_table", [])
    ctx["pop_stats"] = insight.load_meta(db_path, "pop_stats", {})
    ctx["upset_calib"] = insight.load_meta(db_path, "upset_calib", {})
    ctx["comp_tables"] = insight.load_meta(db_path, "comp_tables", {})
    ctx["index_blend"] = insight.load_meta(db_path, "index_blend", {})
    ctx["million_meta"] = insight.load_meta(db_path, "million_rules", {})
    ctx["wins"] = million.load_wins(db_path)
    ctx["figs"] = insight.figures_index(ctx["hist"].df)
    ctx["pace_map"] = insight.realized_pace_table(db_path)
    ctx["horse_hist"] = ctx["hist"].horse
    return ctx


def _write(date, race_rows, pred_rows, db_path):
    races = pd.DataFrame(race_rows)
    # 生の推定 (_raw_*) は較正の学習用なので書き込まない
    races = races[[c for c in races.columns if not c.startswith("_")]]
    preds = pd.concat(pred_rows, ignore_index=True)
    for c in PRED_COLS:
        if c not in preds.columns:
            preds[c] = None
    preds = preds[PRED_COLS].replace({np.nan: None})
    conn = sqlite3.connect(db_path, timeout=120)
    try:
        # site_preds を先に消す (site_races を先に消すと対象が見つからなくなる)
        conn.execute(
            "DELETE FROM site_preds WHERE race_id IN "
            "(SELECT race_id FROM site_races WHERE date=?)", (str(date),))
        conn.execute("DELETE FROM site_races WHERE date=?", (str(date),))
        races.to_sql("site_races", conn, if_exists="append", index=False)
        preds.to_sql("site_preds", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()
    return len(races)


def run(date, db_path=DB_PATH, model_path=MODEL_PATH, race_ids=None,
        progress=print, ctx=None, stage="出馬表"):
    """指定日のレースを予想して site_races / site_preds に入れる。

    stage="登録" のときは特別登録 (TK) の登録馬で予想する。
    """
    if stage == "登録":
        race_ids = race_ids or toku_races(date, db_path)
    else:
        race_ids = race_ids or discover(date, db_path, progress)
    if not race_ids:
        progress(f"{date} に予想できるレースは見つかりませんでした。")
        return {"races": 0}

    progress(f"{date}: {len(race_ids)}レースを予想します ({stage}段階)")
    ctx = ctx or load_ctx(db_path, model_path)
    as_of = pd.Timestamp(date)

    race_rows, pred_rows, skipped = [], [], []
    for i, race_id in enumerate(race_ids, 1):
        try:
            if stage == "登録":
                info, entries = toku_entries(race_id, date, db_path, ctx["hist"])
                out = None if entries is None else score(race_id, info, entries, ctx, as_of)
            else:
                info, out = predict_one(race_id, ctx, as_of)
        except Exception as e:
            skipped.append(f"{race_id}: {e}")
            continue
        if out is None:
            skipped.append(f"{race_id}: 出馬表が空")
            continue
        meta = race_meta(race_id, db_path)
        info = {**info, "date": str(date), "weather": None,
                "post_time": meta.get("post_time"), "grade": meta.get("grade")}
        extras, extra_cols = enrich(out, as_of, ctx)
        row = race_row(race_id, out, info, pop_stats=ctx["pop_stats"],
                       extras=extras, extra_cols=extra_cols,
                       upset_calib=ctx.get("upset_calib"))
        row["stage"] = stage
        h = out.assign(date=str(date), place=info.get("place"), race_no=info.get("race_no"),
                       race_name=info.get("race_name"), surface=info.get("surface"),
                       distance=info.get("distance"), going=info.get("going"),
                       grade=info.get("grade"), is_nar=0)
        mm = million.annotate(h, ctx.get("million_meta"), ctx["wins"])
        if race_id in mm.index:
            row["million_n"] = int(mm.loc[race_id, "million_n"])
            row["million_rules"] = mm.loc[race_id, "million_rules"]
        if stage == "登録":
            row["reason"] += f" ／ 特別登録{len(out)}頭の段階（出走馬・枠順・騎手は未確定）"
        elif out["odds"].isna().all():
            row["reason"] += " ／ オッズ未発表のため能力モデルのみ"
        race_rows.append(row)
        pred_rows.append(out)
        progress(f"[{i}/{len(race_ids)}] {info['place']}{info['race_no']}R "
                 f"◎{out.iloc[0]['horse_name']}")

    if not race_rows:
        progress("予想できたレースがありませんでした。")
        for s_ in skipped[:5]:
            progress(f"  {s_}")
        return {"races": 0}

    n = _write(date, race_rows, pred_rows, db_path)
    progress(f"{n}レースを site_races に追加しました"
             + (f" (見送り {len(skipped)}件)" if skipped else ""))
    return {"races": n, "skipped": skipped}


def run_week(start=None, days=7, db_path=DB_PATH, model_path=MODEL_PATH,
             progress=print):
    """今日から days 日分の開催を全部予想する (土日の両方を出すため)。

    出馬表が入っている日は全レース、特別登録しか無い日は特別レースだけ。
    どちらも JRA-VAN から取り込んだデータを使うので netkeiba を探索しない。
    """
    start = start or dt.date.today()
    ctx, done = None, []
    for d in range(days):
        date = start + dt.timedelta(days=d)
        full = known_races(date, db_path)
        toku = [] if full else toku_races(date, db_path)
        if not full and not toku:
            continue
        ctx = ctx or load_ctx(db_path, model_path)
        stage = "出馬表" if full else "登録"
        res = run(date, db_path, model_path, race_ids=list(full) or toku,
                  progress=progress, ctx=ctx, stage=stage)
        done.append((str(date), stage, res.get("races", 0)))
    if not done:
        progress("この先1週間に、出馬表も特別登録もまだありません。")
    return done


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
    ap.add_argument("--week", action="store_true",
                    help="今日から7日分 (出馬表のある日は全レース、無い日は特別登録)")
    args = ap.parse_args()

    if args.week:
        for date, stage, n in run_week():
            print(f"{date}: {stage}段階 {n}レース")
        return
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
