"""市場とのずれを狙う「直前の買い候補」(単勝)。

考え方 (Benter 型): 発走5分前の単勝オッズから出した市場の確率を土台に、
市場がまだ織り込み切れていない材料でどれだけずれるかを学ぶ。

    P(勝つ) ∝ exp( a・log市場確率 + Σ b_k・x_k )      (レース内で合計1。条件付きロジット)

材料 x (どれも発走5分前に分かる):
  直前の動き   log(5分前オッズ / 40分前オッズ)、40分前→5分前で大きく人気を落とした実力馬
  前日からの動き log(40分前 / 前日最終)
  脚質         近5走の1コーナーの位置 (前ほど小さい)、先行馬か
  当日の馬場   その日・その場・同じ芝ダの、それまでのレースの3着内馬の1角位置 (前残りの日か)、脚質との掛け算
  頭数

買い方: 期待値 = P × 5分前オッズ がしきい値以上の馬の単勝を100円。払戻は確定オッズ。
答え合わせは年ごとの前向き検証 (その年より前だけで学んで、その年を買う)。

  python -m keiba.value            # 年ごとの検証を表示し、最新の係数を site_meta['value_model'] に保存
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

from .scraper import DB_PATH

# レース全体で同じ値 (頭数・その日の前残り度そのもの) はレース内の比べ合いでは効かないので入れない。
# 当日の馬場は脚質との掛け算で入れる
# 近走・騎手などの公開情報 (レースの中で標準化して入れる)。市場は「騎手・通算成績の良い馬を買いすぎ、
# 前走大敗の馬を嫌いすぎ」る傾向があり、その逆を拾う (scripts/value_sweep2.py)
EXTRA = ["prev_agari_rank_r", "prev_to_win", "prev_flow_bad", "prev_pos_late", "avg_agari_rank_r5",
         "days_since_last", "weight_diff", "jockey_top3_rate", "career_top3_rate", "last_finish",
         "workout_lap1f", "dist_change", "kinryo"]
FEATS = ["lpm", "mv5", "mv_pre", "drift_good", "steam", "style", "front", "front_bias", "back_bias"]     + ["z_" + c for c in EXTRA]
L2 = 3.0
EV_MIN = 1.00          # 買う期待値のしきい値 (5分前オッズで)
ODDS_MAX = 30.0        # 5分前オッズがこれより大きい馬は買わない (当たりが少なく、ぶれが大きい)
FIRST_YEAR = 2020


# ---------------------------------------------------------------- 材料づくり
def _hist_odds(conn, tags=("pre", "t40", "t5")):
    out = {}
    for tag in tags:
        rows = conn.execute("SELECT race_id, win FROM odds_hist WHERE tag=? AND win IS NOT NULL",
                            (tag,)).fetchall()
        recs = []
        for rid, win in rows:
            for i, v in enumerate(win.split(",")):
                if v:
                    recs.append((rid, i + 1, float(v)))
        out[tag] = pd.DataFrame(recs, columns=["race_id", "horse_no", tag])
    return out


def style_table(conn, until=None):
    """各馬の近5走の1コーナー位置 (0=先頭〜1=最後方) の平均。results の全期間から、その出走より前で。"""
    r = pd.read_sql("SELECT race_id, date, horse_id, horse_no, finish, corner1 FROM results "
                    "WHERE horse_id IS NOT NULL", conn)
    r["fs"] = r.groupby("race_id")["finish"].transform("size")
    r["c1r"] = (r["corner1"] - 1) / (r["fs"] - 1).clip(lower=1)
    r = r.sort_values(["horse_id", "date"])
    r["style"] = r.groupby("horse_id")["c1r"].transform(
        lambda s: s.shift().rolling(5, min_periods=2).mean())
    return r


def day_bias(r):
    """同じ日・同じ場・同じ芝ダの、それより前のレースの3着内馬の1角位置の平均 (前残り度)。"""
    top = r[r["finish"] <= 3].groupby(["date", "place", "surface", "race_no"])["c1r"].mean() \
        .rename("t3").reset_index().sort_values("race_no")
    g = top.groupby(["date", "place", "surface"])
    top["bias"] = g["t3"].transform(lambda s: s.shift().expanding().mean())
    top["n_prev"] = g.cumcount()
    return top[["date", "place", "surface", "race_no", "bias", "n_prev"]]


def add_feats(d):
    """d: race_id, horse_no, o5, o40, pre, style, bias, n_prev, fs を持つ1頭1行 → FEATS を足す。"""
    d = d.copy()
    inv = 1 / d["o5"]
    d["pm"] = inv / inv.groupby(d["race_id"]).transform("sum")
    d["lpm"] = np.log(d["pm"])
    d["mv5"] = np.log(d["o5"] / d["o40"]).clip(-1.5, 1.5).fillna(0)
    d["mv_pre"] = np.log(d["o40"] / d["pre"]).clip(-1.5, 1.5).fillna(0)
    # 直前に人気を落とした実力馬 (5分前でも10倍以下) / 直前に大きく売れた馬
    d["drift_good"] = ((d["o5"] / d["o40"] >= 1.4) & (d["o5"] <= 10)).astype(float)
    d["steam"] = (d["o5"] / d["o40"] <= 0.7).astype(float)
    st = d["style"].fillna(0.5)
    d["style"] = st
    d["front"] = (st <= 0.3).astype(float)
    have_bias = d["n_prev"].fillna(0) >= 3
    b = d["bias"].where(have_bias, 0.4).fillna(0.4)   # 情報が無いときは平均的な値
    d["bias"] = b - 0.4
    d["front_bias"] = d["front"] * (0.4 - b).clip(lower=0) * have_bias
    d["back_bias"] = (st >= 0.55).astype(float) * (b - 0.45).clip(lower=0) * have_bias
    for c in EXTRA:
        v = pd.to_numeric(d[c], errors="coerce") if c in d else pd.Series(np.nan, index=d.index)
        g = v.groupby(d["race_id"])
        z = (v - g.transform("mean")) / g.transform("std").replace(0, np.nan)
        d["z_" + c] = z.fillna(0).clip(-3, 3)
    return d


def build_history(db_path=DB_PATH):
    """2020年〜の終わったレースの1頭1行 (材料・結果・払戻つき)。"""
    conn = sqlite3.connect(db_path)
    try:
        r = style_table(conn)
        info = pd.read_sql("SELECT race_id, place, race_no, surface, is_nar, odds, popularity, horse_no "
                           "FROM results", conn)
        r = r.merge(info, on=["race_id", "horse_no"], how="left")
        r = r[(r["is_nar"] == 0) & (r["date"] >= f"{FIRST_YEAR}-01-01") & r["surface"].isin(["芝", "ダ"])]
        r = r.merge(day_bias(r), on=["date", "place", "surface", "race_no"], how="left")
        for tag, t in _hist_odds(conn).items():
            r = r.merge(t, on=["race_id", "horse_no"], how="left")
        fk = pd.read_sql("SELECT race_id, CAST(combo AS INT) horse_no, pay FROM payouts "
                         "WHERE bet_type='複勝'", conn)
        r = r.merge(fk, on=["race_id", "horse_no"], how="left")
    finally:
        conn.close()
    r = r.rename(columns={"t5": "o5", "t40": "o40"})
    r = r.merge(extra_table(), on=["race_id", "horse_no"], how="left")
    r = r[r["o5"].notna() & r["o40"].notna() & r["finish"].notna()]
    # 取消などで5分前オッズが欠けた馬が出たレースは、確率の合計が狂うので外す
    r = r[r.groupby("race_id")["o5"].transform("size") == r["fs"]]
    r = add_feats(r)
    r["win"] = (r["finish"] == 1).astype(float)
    r["year"] = r["date"].str[:4].astype(int)
    return r.reset_index(drop=True)


def extra_table():
    """過去レースの近走・騎手などの特徴量 (keiba.features と同じ作り方)。作るのに数分かかるのでキャッシュする。"""
    from pathlib import Path

    from .features import build_training_data, load_horses, load_results
    root = Path(__file__).resolve().parent.parent
    cache = root / "data" / "refine_cache.pkl"
    if cache.exists() and cache.stat().st_mtime > (root / "keiba" / "features.py").stat().st_mtime             and cache.stat().st_mtime > (root / "data" / "keiba.db").stat().st_mtime - 8 * 86400:
        X, meta = pd.read_pickle(cache)
    else:
        X, meta = build_training_data(load_results(), load_horses())
        pd.to_pickle((X, meta), cache)
    out = X[EXTRA].copy()
    out["race_id"] = meta["race_id"].values
    out["horse_no"] = pd.to_numeric(meta["horse_no"], errors="coerce").values
    return out


# ---------------------------------------------------------------- 条件付きロジット
def _design(d):
    return d[FEATS].to_numpy(float)


def fit(d, l2=L2):
    """レース内ソフトマックスの尤度を最大にする係数。"""
    from scipy.optimize import minimize

    Xf = _design(d)
    y = d["win"].to_numpy()
    codes, idx = np.unique(d["race_id"].to_numpy(), return_inverse=True)
    n = len(codes)
    has_win = np.bincount(idx, weights=y, minlength=n) > 0

    def nll(w):
        z = Xf @ w
        zmax = np.full(n, -np.inf)
        np.maximum.at(zmax, idx, z)
        e = np.exp(z - zmax[idx])
        s = np.bincount(idx, weights=e, minlength=n)
        lse = np.log(s) + zmax
        ll = (z * y).sum() - lse[has_win].sum()
        p = e / s[idx]
        grad_ll = Xf.T @ y - Xf.T @ (p * has_win[idx])
        # 土台 (lpm の係数) 以外は 0 に向けて少し縮める
        reg = l2 * ((w[1:] ** 2).sum())
        g = -grad_ll + np.r_[0, 2 * l2 * w[1:]]
        return -ll + reg, g

    w0 = np.zeros(len(FEATS))
    w0[0] = 1.0
    res = minimize(nll, w0, jac=True, method="L-BFGS-B")
    return dict(zip(FEATS, map(float, res.x)))


def predict(d, coef):
    z = _design(d) @ np.array([coef[f] for f in FEATS])
    z = pd.Series(z, index=d.index)
    e = np.exp(z - z.groupby(d["race_id"]).transform("max"))
    return e / e.groupby(d["race_id"]).transform("sum")


def bets(d, p, ev_min=EV_MIN, odds_max=ODDS_MAX):
    ev = p * d["o5"]
    return (ev >= ev_min) & (d["o5"] <= odds_max)


def walk_forward(h, years=None, ev_min=EV_MIN, log=print):
    """年ごとに「その年より前で学習 → その年を買う」。"""
    years = years or sorted(y for y in h["year"].unique() if y >= FIRST_YEAR + 2)
    rows = []
    for y in years:
        tr, te = h[h["year"] < y], h[h["year"] == y].copy()
        coef = fit(tr)
        te["p"] = predict(te, coef)
        b = te[bets(te, te["p"], ev_min)]
        ret = np.where(b["win"] == 1, b["odds"] * 100, 0)
        n = len(b)
        roi = ret.sum() / (100 * n) if n else None
        # 回収率の偶然の幅 (1頭100円の払戻のばらつきから)
        se = (ret / 100).std(ddof=1) / np.sqrt(n) if n > 1 else None
        # 比べる相手: 市場の確率どおり (係数 a=1、他0) で同じ買い方
        base = te[bets(te, te["pm"], ev_min)]
        rows.append({"year": int(y), "races": int(te["race_id"].nunique()), "bets": int(n),
                     "hits": int((b["win"] == 1).sum()), "roi": round(float(roi), 4) if roi else None,
                     "se": round(float(se), 4) if se else None,
                     "avg_odds": round(float(b["o5"].mean()), 1) if n else None,
                     "all_roi": round(float(np.where(te["win"] == 1, te["odds"], 0).mean()), 4),
                     "base_bets": int(len(base))})
        log(f"{y}: {n}点 的中{rows[-1]['hits']} 回収率 {roi:.1%} (±{2 * (se or 0):.1%}) "
            f"平均オッズ {rows[-1]['avg_odds']} / 全頭 {rows[-1]['all_roi']:.1%}")
    return rows


def main(save=True):
    h = build_history()
    print(f"{h['race_id'].nunique()}レース {len(h)}頭 ({h['date'].min()}〜{h['date'].max()})")
    rows = walk_forward(h)
    tot_b = sum(r["bets"] for r in rows)
    tot_r = sum((r["roi"] or 0) * r["bets"] for r in rows) / max(tot_b, 1)
    print(f"通算 {tot_b}点 回収率 {tot_r:.1%}")
    coef = fit(h)
    print("係数 (全期間):", {k: round(v, 3) for k, v in coef.items()})
    if save:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("CREATE TABLE IF NOT EXISTS site_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO site_meta VALUES ('value_model', ?)", (json.dumps({
            "coef": coef, "ev_min": EV_MIN, "odds_max": ODDS_MAX, "walk_forward": rows,
            "total": {"bets": tot_b, "roi": round(tot_r, 4)},
            "fit_until": h["date"].max()}, ensure_ascii=False),))
        conn.commit()
        conn.close()
    return rows, coef


if __name__ == "__main__":
    main(save="--nosave" not in sys.argv)


# ---------------------------------------------------------------- 当日 (発走の数分前) の判定
VALUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS value_feats (
    race_id TEXT, horse_no INTEGER, horse_id TEXT, feats TEXT, PRIMARY KEY (race_id, horse_no));
CREATE TABLE IF NOT EXISTS value_picks (
    race_id TEXT, horse_no INTEGER, horse_name TEXT, date TEXT, at TEXT,
    o5 REAL, p REAL, ev REAL, pm REAL, reason TEXT, PRIMARY KEY (race_id, horse_no));
CREATE TABLE IF NOT EXISTS value_checked (race_id TEXT PRIMARY KEY, at TEXT, n_horses INTEGER, n_picks INTEGER);
"""


def save_feats(conn, race_id, out):
    """予想のときの特徴量 (EXTRA) を保存しておく (直前の判定で使う)。out: upcoming.score の結果"""
    conn.executescript(VALUE_SCHEMA)
    rows = []
    for _, r in out.iterrows():
        f = {c: (None if pd.isna(r.get("vx_" + c)) else float(r.get("vx_" + c))) for c in EXTRA}
        rows.append((race_id, int(r["horse_no"]), r.get("horse_id"), json.dumps(f)))
    conn.executemany("INSERT OR REPLACE INTO value_feats VALUES (?,?,?,?)", rows)


def _odds_vec(text):
    return {i + 1: float(v) for i, v in enumerate((text or "").split(",")) if v}


def live_frame(conn, race_id, date, post_dt):
    """発走直前のレースの1頭1行 (材料つき)。足りなければ None。"""
    import datetime as dt

    conn.executescript(VALUE_SCHEMA)
    ent = pd.read_sql("SELECT horse_no, horse_id, horse_name FROM entries WHERE race_id=?",
                      conn, params=(race_id,))
    if len(ent) < 4:
        return None
    snaps = conn.execute("SELECT at, win FROM odds_live WHERE race_id=? ORDER BY at", (race_id,)).fetchall()
    if not snaps:
        return None
    o5 = _odds_vec(snaps[-1][1])
    lim40 = (post_dt - dt.timedelta(minutes=35)).strftime("%Y-%m-%d %H:%M")
    early = [w for at, w in snaps if at <= lim40]
    o40 = _odds_vec(early[-1] if early else snaps[0][1])
    pre_row = conn.execute("SELECT win FROM odds_hist WHERE race_id=? AND tag='pre'", (race_id,)).fetchone()
    pre = _odds_vec(pre_row[0]) if pre_row else {}
    d = ent.copy()
    d["race_id"] = race_id
    d["o5"] = d["horse_no"].map(o5)
    d["o40"] = d["horse_no"].map(o40).fillna(d["o5"])
    d["pre"] = d["horse_no"].map(pre)
    d = d[d["o5"].notna()]
    if len(d) < 4:
        return None
    d["fs"] = len(d)
    # 脚質: 近5走の1コーナー位置
    ids = [h for h in d["horse_id"].dropna().unique()]
    st = {}
    if ids:
        hist = pd.read_sql(
            f"SELECT r.horse_id, r.date, r.corner1, (SELECT COUNT(*) FROM results x WHERE x.race_id=r.race_id) fs "
            f"FROM results r WHERE r.horse_id IN ({','.join('?' * len(ids))}) AND r.date < ?",
            conn, params=ids + [str(date)])
        hist["c1r"] = (hist["corner1"] - 1) / (hist["fs"] - 1).clip(lower=1)
        for h, g in hist.sort_values("date").groupby("horse_id"):
            v = g["c1r"].dropna().tail(5)
            if len(v) >= 2:
                st[h] = float(v.mean())
    d["style"] = d["horse_id"].map(st)
    # 当日の馬場: 同じ場・同じ芝ダの、それまでのレースの3着内馬の1角位置
    info = conn.execute("SELECT place, surface, race_no FROM races WHERE race_id=?", (race_id,)).fetchone()
    bias, n_prev = None, 0
    if info:
        today = pd.read_sql(
            "SELECT race_id, race_no, finish, corner1 FROM results WHERE date=? AND place=? AND surface=? "
            "AND race_no < ?", conn, params=(str(date), info[0], info[1], info[2]))
        if len(today):
            today["fs"] = today.groupby("race_id")["finish"].transform("size")
            today["c1r"] = (today["corner1"] - 1) / (today["fs"] - 1).clip(lower=1)
            t3 = today[today["finish"] <= 3].groupby("race_id")["c1r"].mean()
            n_prev, bias = len(t3), (float(t3.mean()) if len(t3) else None)
    d["bias"], d["n_prev"] = bias, n_prev
    fe = dict(conn.execute("SELECT horse_no, feats FROM value_feats WHERE race_id=?", (race_id,)).fetchall())
    for c in EXTRA:
        d[c] = [json.loads(fe[n]).get(c) if n in fe else None for n in d["horse_no"]]
    return add_feats(d)


def load_model(conn):
    row = conn.execute("SELECT value FROM site_meta WHERE key='value_model'").fetchone()
    return json.loads(row[0]) if row else None


def judge(conn, race_id, date, post_dt, now=None):
    """発走の数分前に呼ぶ。買い候補を value_picks に書き、候補の数を返す。"""
    import datetime as dt

    model = load_model(conn)
    d = live_frame(conn, race_id, date, post_dt)
    stamp = (now or dt.datetime.now()).strftime("%Y-%m-%d %H:%M")
    if model is None or d is None:
        conn.execute("INSERT OR REPLACE INTO value_checked VALUES (?,?,?,?)", (race_id, stamp, 0, 0))
        conn.commit()
        return 0
    d["p"] = predict(d, model["coef"])
    d["ev"] = d["p"] * d["o5"]
    pick = d[bets(d, d["p"], model.get("ev_min", EV_MIN), model.get("odds_max", ODDS_MAX))]
    conn.execute("DELETE FROM value_picks WHERE race_id=?", (race_id,))
    for r in pick.itertuples():
        why = []
        if r.drift_good:
            why.append("直前に人気を落とした実力馬")
        if r.mv5 <= -0.2:
            why.append("直前に売れている")
        if r.front and r.front_bias > 0:
            why.append("前残りの馬場で先行馬")
        if r.z_last_finish >= 1:
            why.append("前走大敗で人気が落ちすぎ")
        if r.z_jockey_top3_rate <= -1:
            why.append("騎手人気で過剰に売れていない")
        conn.execute("INSERT OR REPLACE INTO value_picks VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (race_id, int(r.horse_no), r.horse_name, str(date), stamp, float(r.o5),
                      float(r.p), float(r.ev), float(r.pm), "・".join(why)))
    conn.execute("INSERT OR REPLACE INTO value_checked VALUES (?,?,?,?)", (race_id, stamp, len(d), len(pick)))
    conn.commit()
    return len(pick)
