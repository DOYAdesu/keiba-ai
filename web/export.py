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


def _review(conn, db_path, where):
    """回顧と前走メモを作る (web/review.py)。"""
    from web import review

    rows = _rows(conn, f"SELECT race_id, date FROM site_races {where}")
    dates = {r["race_id"]: r["date"] for r in rows}
    conn.row_factory = None
    horses = {}
    for sql in (
        "SELECT p.race_id, p.horse_no, x.horse_id FROM site_preds p "
        "JOIN results x ON x.race_id = p.race_id AND x.horse_no = p.horse_no",
        "SELECT p.race_id, p.horse_no, x.horse_id FROM site_preds p "
        "JOIN entries x ON x.race_id = p.race_id AND x.horse_no = p.horse_no",
        "SELECT p.race_id, p.horse_no, x.horse_id FROM site_preds p JOIN site_races s "
        "ON s.race_id = p.race_id AND s.stage = '登録' "
        "JOIN tokubetsu x ON x.race_id = p.race_id AND x.num = p.horse_no",
    ):
        for rid, no, hid in conn.execute(sql):
            if rid in dates and hid:
                horses.setdefault((rid, int(no)), hid)
    races, entries, stats = review.site_payload(db_path, list(dates), dates, horses)
    return races, entries, stats


def _day_stage(races):
    """開催日の段階。当日の直前更新 (直前・当日) が混ざっても「出馬表」として扱う。"""
    st = {r.get("stage") for r in races if r.get("stage")}
    if not st:
        return None
    if "登録" in st:
        return "登録"
    return "出馬表"


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


def value_payload(conn, race_ids):
    """直前の買い候補 (keiba/value.py) をレースごとに。"""
    if not race_ids:
        return {}
    ph = ",".join("?" * len(race_ids))
    conn.row_factory = None
    try:
        rows = conn.execute(f"SELECT race_id, horse_no, horse_name, at, o5, p, ev, reason FROM value_picks "
                            f"WHERE race_id IN ({ph}) ORDER BY ev DESC", race_ids).fetchall()
        checked = dict(conn.execute(f"SELECT race_id, at FROM value_checked WHERE race_id IN ({ph})",
                                    race_ids).fetchall())
    except sqlite3.OperationalError:
        return {}
    out = {rid: {"at": at, "picks": []} for rid, at in checked.items()}
    for rid, no, name, at, o5, p, ev, reason in rows:
        out.setdefault(rid, {"at": at, "picks": []})["picks"].append(
            {"no": no, "name": name, "o5": round(o5, 1), "p": round(p, 4), "ev": round(ev, 3),
             "why": reason or ""})
    return out


def value_record(conn):
    """直前の買い候補の実績 (実際に判定したもの) と、年ごとの前向き検証。"""
    conn.row_factory = None
    try:
        row = conn.execute("SELECT value FROM site_meta WHERE key='value_model'").fetchone()
    except sqlite3.OperationalError:
        return None
    try:   # 実際の判定の記録 (当日更新が始まるまでは表が無い)
        live = conn.execute(
            "SELECT v.date, v.o5, x.finish, x.odds FROM value_picks v "
            "JOIN results x ON x.race_id=v.race_id AND x.horse_no=v.horse_no").fetchall()
    except sqlite3.OperationalError:
        live = []
    if not row:
        return None
    m = json.loads(row[0])
    n = len(live)
    ret = sum((odds or 0) * 100 for _, _, fin, odds in live if fin == 1)
    return {"walk_forward": m.get("walk_forward"), "total": m.get("total"),
            "ev_min": m.get("ev_min"), "odds_max": m.get("odds_max"),
            "live": {"bets": n, "hits": sum(1 for _, _, f, _ in live if f == 1),
                     "roi": round(ret / (100 * n), 4) if n else None,
                     "from": min((d for d, *_ in live), default=None)}}


def patch_value_picks(date, db_path=DB_PATH, out_dir=DIST):
    """当日の直前判定のあと、その日のファイルだけ書き換える (全部の書き出しは数分かかるため)。"""
    path = Path(out_dir) / "data" / "day" / f"{date}.json"
    if not path.exists():
        return False
    day = json.loads(path.read_text(encoding="utf-8"))
    conn = sqlite3.connect(db_path)
    try:
        vp = value_payload(conn, [r["race_id"] for r in day["races"]])
    finally:
        conn.close()
    for r in day["races"]:
        if r["race_id"] in vp:
            r["value"] = vp[r["race_id"]]
    path.write_text(json.dumps(day, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return True


def period_stats(conn, jra_only=True):
    """成績ページ: 月別・年別の 本命 / 堅軸 / 本命★ / 1番人気 の的中率と回収率。

    回収率は100円均等買い。単勝は確定オッズ、複勝は実際の払戻。
    学習期間 (in_sample=1) は実力より良く出るので、検証期間と分けて集計する。
    """
    import pandas as pd

    where = "AND r.is_nar=0" if jra_only else ""
    conn.row_factory = None
    top = pd.read_sql(
        f"SELECT r.race_id, r.date, r.in_sample, r.is_rec, r.top_finish AS fin, r.top_odds AS odds, "
        f"r.top_fuku_pay AS fuku, r.top_ev AS ev FROM site_races r "
        f"WHERE r.top_finish IS NOT NULL {where}", conn)
    # 比べる相手は「発走40分前の1番人気」(AIの本命と同じ時点。確定の1番人気は後出しになる)
    from keiba.oddsmove import odds_at
    allp = pd.read_sql(
        f"SELECT p.race_id, p.horse_no, r.date, r.in_sample, p.finish AS fin, p.odds, p.popularity "
        f"FROM site_preds p JOIN site_races r ON r.race_id=p.race_id "
        f"WHERE p.finish IS NOT NULL {where}", conn)
    allp["o40"] = odds_at(allp).fillna(allp["odds"]).values
    fav = allp.sort_values(["race_id", "o40"]).drop_duplicates("race_id")
    fpay = pd.read_sql("SELECT race_id, CAST(combo AS INT) AS horse_no, pay AS fuku FROM payouts "
                       "WHERE bet_type='複勝'", conn)
    fav = fav.merge(fpay, on=["race_id", "horse_no"], how="left")
    groups = {
        "本命": top,
        "堅軸": top[top["is_rec"] == 1],
        "本命★": top[top["ev"] >= 1.0],
        "1番人気": fav,
    }

    def agg(g):
        if not len(g):
            return None
        win = g["fin"] == 1
        return {"n": int(len(g)),
                "win": round(float(win.mean()), 4),
                "top3": round(float((g["fin"] <= 3).mean()), 4),
                "tan": round(float((g["odds"].where(win, 0).fillna(0) * 100).sum() / len(g) / 100), 4),
                "fuku": round(float(g["fuku"].fillna(0).sum() / len(g) / 100), 4)}

    out = {"groups": list(groups), "months": [], "years": [], "periods": []}
    for key, label in (("month", 7), ("year", 4)):
        rows = []
        for per in sorted({d[:label] for d in top["date"]}, reverse=True):
            row = {"period": per}
            for name, g in groups.items():
                gg = g[g["date"].str[:label] == per]
                row[name] = agg(gg)
            tt = top[top["date"].str[:label] == per]
            # 検証期間の割合 (1=全部が検証期間、0=全部が学習期間)
            row["valid_share"] = round(float((tt["in_sample"] == 0).mean()), 2) if len(tt) else None
            rows.append(row)
        out["months" if key == "month" else "years"] = rows
    # 直近の開催 (最後の2開催日 = たいてい先週の土日)
    last = sorted(set(top["date"]))[-2:]
    if last:
        row = {"period": "・".join(last), "dates": last}
        for name, g in groups.items():
            row[name] = agg(g[g["date"].isin(last)])
        out["recent"] = row
    for flag, label in ((0, "検証期間"), (1, "学習期間")):
        row = {"period": label, "valid_share": 1.0 - flag}
        for name, g in groups.items():
            row[name] = agg(g[g["in_sample"] == flag])
        out["periods"].append(row)
    return out


def past_runs(conn, days, n=5):
    """出馬表の「近走」(馬柱)。days の各レースの出走馬について、そのレースより前の近5走。

    返り値: {date: {race_id: {horse_no: [[日付, 場, レース名, コース, 頭数, 着順, 人気, 上がり, 上がり順位, 着差, 1角], ...]}}}
    """
    import pandas as pd

    if not days:
        return {}
    conn.row_factory = None
    ph = ",".join("?" * len(days))
    targets = pd.read_sql(
        f"SELECT p.race_id, p.horse_no, r.date, "
        f"COALESCE(x.horse_id, e.horse_id, t.horse_id) AS horse_id "
        f"FROM site_preds p JOIN site_races r ON r.race_id=p.race_id "
        f"LEFT JOIN results x ON x.race_id=p.race_id AND x.horse_no=p.horse_no "
        f"LEFT JOIN entries e ON e.race_id=p.race_id AND e.horse_no=p.horse_no "
        f"LEFT JOIN tokubetsu t ON t.race_id=p.race_id AND t.num=p.horse_no AND r.stage='登録' "
        f"WHERE r.date IN ({ph})", conn, params=list(days))
    targets = targets.dropna(subset=["horse_id"])
    if targets.empty:
        return {}
    hids = list(targets["horse_id"].unique())
    res = []
    for i in range(0, len(hids), 900):
        chunk = hids[i:i + 900]
        res.append(pd.read_sql(
            f"SELECT horse_id, race_id, date, place, race_name, surface, distance, finish, "
            f"popularity, agari, time_sec, corner1 FROM results "
            f"WHERE horse_id IN ({','.join('?' * len(chunk))})", conn, params=chunk))
    res = pd.concat(res, ignore_index=True)
    # レース内の頭数・上がり順位・勝ち馬との差は、そのレースの全頭から出す
    rids = list(res["race_id"].unique())
    allr = []
    for i in range(0, len(rids), 900):
        chunk = rids[i:i + 900]
        allr.append(pd.read_sql(
            f"SELECT race_id, finish, agari, time_sec FROM results "
            f"WHERE race_id IN ({','.join('?' * len(chunk))})", conn, params=chunk))
    allr = pd.concat(allr, ignore_index=True)
    rs = allr.groupby("race_id").agg(fs=("finish", "size"), best=("time_sec", "min"))
    res = res.join(rs, on="race_id")
    ag = allr.dropna(subset=["agari"])
    res["ag_rank"] = [None] * len(res)
    rank_map = {}
    for rid, g in ag.groupby("race_id"):
        rank_map[rid] = sorted(g["agari"].tolist())
    res["ag_rank"] = [
        (rank_map[r].index(a) + 1) if r in rank_map and a == a and a is not None else None
        for r, a in zip(res["race_id"], res["agari"])]
    res["margin"] = (res["time_sec"] - res["best"]).round(1)
    res = res.sort_values("date", ascending=False)
    by_horse = {h: g for h, g in res.groupby("horse_id", sort=False)}

    def clean(v):
        return None if v is None or v != v else (int(v) if isinstance(v, float) and v.is_integer() else v)

    out = {}
    for t in targets.itertuples():
        g = by_horse.get(t.horse_id)
        if g is None:
            continue
        g = g[g["date"] < t.date].head(n)
        if g.empty:
            continue
        runs = [[clean(r.date), clean(r.place), (r.race_name or "")[:12],
                 f"{r.surface or ''}{clean(r.distance) or ''}", clean(r.fs), clean(r.finish),
                 clean(r.popularity), clean(r.agari), clean(r.ag_rank), clean(r.margin),
                 clean(r.corner1)] for r in g.itertuples()]
        out.setdefault(t.date, {}).setdefault(t.race_id, {})[int(t.horse_no)] = runs
    return out


PAST_DAYS = 70   # 近走 (馬柱) を書き出す範囲: これからの開催と、直近この日数


HIST_LABEL = [("pre", "前日最終"), ("am", "当日朝"), ("t60", "60分前"), ("t40", "40分前"),
              ("t20", "20分前"), ("t10", "10分前"), ("t5", "5分前"), ("fin", "確定")]
MAX_LIVE_PTS = 16
COMBO_KEEP = {"馬連": 153, "ワイド": 153, "馬単": 306, "三連複": 400, "三連単": 900}


def _csv_nums(t):
    return [float(v) if v else None for v in (t or "").split(",")]


def _odds_payload(conn, race_ids):
    """オッズの動き。終わったレースは時系列の要所、当日のレースは取り込んだ速報。"""
    if not race_ids:
        return {}
    ph = ",".join("?" * len(race_ids))
    conn.row_factory = None
    hist, live = {}, {}
    try:
        for rid, tag, at, win, plo, phi in conn.execute(
                f"SELECT race_id, tag, at, win, plo, phi FROM odds_hist "
                f"WHERE race_id IN ({ph}) AND win IS NOT NULL", race_ids):
            hist.setdefault(rid, {})[tag] = (at, win, plo, phi)
        for rid, at, kubun, win, plo, phi in conn.execute(
                f"SELECT race_id, at, kubun, win, plo, phi FROM odds_live "
                f"WHERE race_id IN ({ph}) ORDER BY race_id, at", race_ids):
            live.setdefault(rid, []).append((at, kubun, win, plo, phi))
    except sqlite3.OperationalError:
        return {}
    out = {}
    for rid in race_ids:
        h, lv = hist.get(rid, {}), live.get(rid, [])
        pts = []
        if "fin" in h:   # 終わったレース: 時系列の要所
            for tag, label in HIST_LABEL:
                if tag in h:
                    pts.append({"t": label, "at": h[tag][0], "w": _csv_nums(h[tag][1])})
            last = h["fin"]
        elif lv:
            for tag, label in HIST_LABEL[:2]:   # 前日最終・当日朝 (速報の時系列から)
                if tag in h and h[tag][0] < lv[0][0]:
                    pts.append({"t": label, "at": h[tag][0], "w": _csv_nums(h[tag][1])})
            # 間引き: 最初と最後は残し、間を均等に
            step = max(1, len(lv) // MAX_LIVE_PTS)
            keep = lv[::step]
            if keep[-1] is not lv[-1]:
                keep.append(lv[-1])
            for at, kubun, win, _, _ in keep:
                label = "確定" if kubun in ("4", "5") else ("最終" if kubun == "3" else at[11:16])
                pts.append({"t": label, "at": at, "w": _csv_nums(win)})
            last = (lv[-1][0], lv[-1][2], lv[-1][3], lv[-1][4])
        else:
            continue
        out[rid] = {"pts": pts, "plo": _csv_nums(last[2]), "phi": _csv_nums(last[3]),
                    "at": last[0], "live": "fin" not in h}
    return out


def _combo_payload(conn, race_ids):
    """全券種のオッズ (発走前の当日のレースだけ)。人気の上位だけ残して軽くする。"""
    if not race_ids:
        return {}
    ph = ",".join("?" * len(race_ids))
    conn.row_factory = None
    out = {}
    try:
        rows = conn.execute(f"SELECT race_id, bet_type, combo, odds, pop, at FROM odds_combo "
                            f"WHERE race_id IN ({ph}) ORDER BY race_id, bet_type, odds", race_ids)
        for rid, bt, combo, odds, pop, at in rows:
            r = out.setdefault(rid, {"at": at})
            d = r.setdefault(bt, {})
            if len(d) < COMBO_KEEP.get(bt, 300):
                d[combo] = odds
    except sqlite3.OperationalError:
        return {}
    return out


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
        # 古い app.js / style.css がブラウザに残らないよう、中身の版を付けて読ませる
        import hashlib
        html = (out_dir / "index.html").read_text(encoding="utf-8")
        for name in ("app.js", "style.css"):
            v = hashlib.md5((STATIC / name).read_bytes()).hexdigest()[:8]
            html = html.replace(f'"{name}"', f'"{name}?v={v}"')
        (out_dir / "index.html").write_text(html, encoding="utf-8")

        # 回顧 (ペース・レースの質・各馬の流れと次走へのメモ) と前走メモ
        rv_races, rv_entries, rv_stats = _review(conn, db_path, where)

        # 馬場 (クッション値・含水率。JRA の公表値)
        baba = {}
        try:
            import pandas as pd
            from keiba.baba import race_baba
            conn.row_factory = None
            rr = pd.read_sql(f"SELECT race_id, date, place, surface FROM site_races {where}", conn)
            for row in race_baba(conn, rr).itertuples():
                d = {k: getattr(row, k) for k in ("cushion", "moist", "moist_4c", "course", "baba_date")}
                d = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in d.items()
                     if v is not None and v == v}
                if "cushion" in d or "moist" in d:
                    baba[row.race_id] = d
        except Exception as e:  # 馬場情報が無くてもサイトは出す
            progress(f"馬場情報を載せられませんでした: {e}")

        index_days = []
        past_days = {d for d in days if d >= str(dt.date.today() - dt.timedelta(days=PAST_DAYS))}
        # 過去の同じレース (同じ条件) の1〜3着の人気。近走と同じく、これからの開催と直近だけ
        from web import same_race
        conn.row_factory = None
        ph = ",".join("?" * len(past_days))
        same_races = same_race.load(conn)
        same = same_race.payload(conn, conn.execute(
            f"SELECT race_id, race_name, date, place, surface, distance FROM site_races "
            f"WHERE date IN ({ph})", sorted(past_days)).fetchall(), same_races) if past_days else {}
        progress(f"過去の同じレース: {len(same)}レース分")
        # 重賞ごとの荒れやすさ (人気どおりに決まらない重賞) の一覧
        try:
            jt = same_race.jushou_table(conn, same_races)
            today = str(dt.date.today())
            jt["upcoming"] = [
                {"date": r["date"], "race_id": r["race_id"], "place": r["place"], "race_no": r["race_no"],
                 "race_name": r["race_name"], "label": same[r["race_id"]]["upset"]["label"],
                 "mult": same[r["race_id"]]["upset"]["mult"], "n": same[r["race_id"]]["upset"]["n"]}
                for r in _rows(conn, "SELECT date, race_id, place, race_no, race_name FROM site_races "
                                     "WHERE date >= ? AND is_nar = 0 ORDER BY date, place, race_no", (today,))
                if same.get(r["race_id"], {}).get("upset", {}).get("group") == "重賞"]
            (out_dir / "data" / "jushou.json").write_text(
                json.dumps(jt, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            progress(f"重賞の荒れやすさ: {len(jt['items'])}レース")
        except Exception as e:  # 一覧が作れなくてもサイトは出す
            progress(f"重賞の荒れやすさを作れませんでした: {e}")
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
                se = _slim(e)
                rv = rv_entries.get((e["race_id"], e["horse_no"]))
                if rv:
                    se["rv"] = {k: v for k, v in rv.items() if v is not None}
                by_race.setdefault(e["race_id"], []).append(se)

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

            ids = [r["race_id"] for r in races]
            odds_mv = _odds_payload(conn, ids)
            combos = _combo_payload(conn, [r["race_id"] for r in races if r.get("winner_no") is None])
            vpicks = value_payload(conn, ids)
            for r in races:
                r["entries"] = by_race.get(r["race_id"], [])
                if r["race_id"] in odds_mv:
                    r["odds_mv"] = odds_mv[r["race_id"]]
                if r["race_id"] in combos:
                    r["combos"] = combos[r["race_id"]]
                if r["race_id"] in baba:
                    r["baba"] = baba[r["race_id"]]
                if r["race_id"] in vpicks:
                    r["value"] = vpicks[r["race_id"]]
                if r["race_id"] in same:
                    r["same"] = same[r["race_id"]]
                if r["race_id"] in pays:
                    r["payouts"] = pays[r["race_id"]]
                if r["race_id"] in rv_races:
                    r["review"] = rv_races[r["race_id"]]
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
                "has_past": date in past_days,
                # 出走前の日は「登録」(特別登録のみ) / 「出馬表」、終わった日は None
                "stage": _day_stage(races),
                "graded": [f"{r['place']}{r['race_no']}R {r['race_name']}"
                           for r in races if r.get("grade") in ("G1", "G2", "G3")],
                "n_million": sum(1 for r in races if (r.get("million_n") or 0) > 0),
            })
            if (i + 1) % 50 == 0 or i + 1 == len(days):
                progress(f"書き出し {i + 1}/{len(days)}日")

        # 近走 (馬柱): これからの開催と直近の開催日だけ (全日分は重いので)
        today = dt.date.today()
        recent = [d for d in days if d >= str(today - dt.timedelta(days=PAST_DAYS))]
        (out_dir / "data" / "past").mkdir(parents=True, exist_ok=True)
        past = past_runs(conn, recent)
        for d, v in past.items():
            (out_dir / "data" / "past" / f"{d}.json").write_text(
                json.dumps(v, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        progress(f"近走 (馬柱) を {len(past)}日分書き出しました")

        # 成績ページ
        stats = period_stats(conn, jra_only)
        (out_dir / "data" / "stats.json").write_text(
            json.dumps(stats, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

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
        srow = _rows(conn, "SELECT value FROM site_meta WHERE key='sim_params'")
        sim_params = json.loads(srow[0]["value"]) if srow else None
        orow = _rows(conn, "SELECT value FROM site_meta WHERE key='odds_stats'")
        odds_stats = json.loads(orow[0]["value"]) if orow else None
        brow = _rows(conn, "SELECT value FROM site_meta WHERE key='baba_now'")
        baba_now = json.loads(brow[0]["value"]) if brow else None
        (out_dir / "data" / "index.json").write_text(
            json.dumps({
                "index_report": index_report,
                "upset_report": upset_report,
                "review_stats": rv_stats,
                "sim_params": sim_params,
                "value": value_record(conn),
                "odds_stats": odds_stats,
                "baba": baba_now,
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
