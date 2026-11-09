"""能力指数の内訳 (走破〜馬場) と指数の算出方法を、過去レースの結果で比べる。

評価: レース内AUC = 「3着以内に来た馬」が「来なかった馬」より高い点を付けられている確率 (0.5=でたらめ)。
      各項目の点が一番高い馬の複勝率も併記する。
選定期間 A: 2025-01-01〜2026-05-18 / 確認期間 B: 2026-05-19〜 (モデルの学習に使っていない)。
どの値も「そのレースより前」の成績だけで作るので、結果を覗いていない。
"""
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

ROOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from keiba.features import load_horses, load_results, normalize_bloodline  # noqa: E402
from web import insight  # noqa: E402

T0 = time.time()
df = load_results()
df = df[df["is_nar"] == 0].copy()
d = insight.run_figures(df).sort_values(["date", "race_id"]).reset_index(drop=True)
m = float(d["_top3"].mean())
fs = d.groupby("race_id")["horse_no"].transform("count")

# --- 1走ごとの追加の数値 ---
tB = d["time_sec"].where(d["time_sec"] > 0)
keyB = [d["place_cd"], d["surface"], d["distance"], d["going_grp"]]
d["spd_zB"] = (tB.groupby(keyB).transform("mean") - tB) / tB.groupby(keyB).transform("std").replace(0, np.nan)
win_time = d[d["finish"] == 1].groupby("race_id")["time_sec"].min()
d["behind"] = (d["time_sec"] - d["race_id"].map(win_time)) / d["distance"] * 1000  # 1000mあたりの着差(秒)
d["agari_rank"] = d.groupby("race_id")["agari"].rank(method="average") / fs
d["dist200"] = d["distance"] // 200
ped = load_horses().drop_duplicates("horse_id").set_index("horse_id")
d["sire"] = d["horse_id"].map(ped["sire_name"]).map(normalize_bloodline)
print(f"準備 {time.time() - T0:.0f}s / {d['race_id'].nunique()}レース")


def roll(col, n, how, by="horse_id"):
    """by ごとに、その行より前の n 走の集計。"""
    s = d.groupby(by, sort=False)[col].shift()
    r = s.groupby(d[by], sort=False).rolling(n, min_periods=1)
    r = getattr(r, how)()
    return r.reset_index(level=0, drop=True).reindex(d.index)


def ewm(col, span, by="horse_id"):
    s = d.groupby(by, sort=False)[col].shift()
    return s.groupby(d[by], sort=False).transform(lambda x: x.ewm(span=span, ignore_na=True).mean())


def cum_rate(keys, k, min_n=1, target="_top3", days=None):
    """keys ごとに、その行より前の率 (全体平均へ k 走ぶん縮める)。days 指定時は直近 days 日だけ。"""
    kk = [d[c].astype(object).where(d[c].notna(), "_na") for c in keys]
    has = d[keys].notna().all(axis=1)
    y = d[target].astype(float)
    if days is None:
        g = y.groupby(kk, sort=False)
        n = g.cumcount()
        s = g.cumsum() - y
    else:
        n_arr = np.full(len(d), np.nan)
        s_arr = np.full(len(d), np.nan)
        dates = d["date"].values.astype("datetime64[D]").astype(np.int64)
        yv = y.to_numpy()
        for _, idx in d.groupby(kk, sort=False).indices.items():
            dt_ = dates[idx]
            cy = np.concatenate([[0.0], np.cumsum(yv[idx])])
            lo = np.searchsorted(dt_, dt_ - days, side="left")
            hi = np.searchsorted(dt_, dt_, side="left")      # 同じ日の他レースは含めない
            n_arr[idx] = hi - lo
            s_arr[idx] = cy[hi] - cy[lo]
        n = pd.Series(n_arr, index=d.index)
        s = pd.Series(s_arr, index=d.index)
    mm = float(y.mean())
    rate = (s + k * mm) / (n + k)
    return rate.where((n >= min_n) & has)


# --- 候補 (値は「大きいほど良い」に揃える) ---
V = {}
V[("走破", "現行: 近5走の最速 (芝ダ・距離・馬場で標準化)")] = roll("spd_z", 5, "max")
V[("走破", "近3走の最速")] = roll("spd_z", 3, "max")
V[("走破", "近5走の平均")] = roll("spd_z", 5, "mean")
V[("走破", "近走ほど重く (指数平滑3)")] = ewm("spd_z", 3)
V[("走破", "競馬場も揃えて標準化・近5走の最速")] = roll("spd_zB", 5, "max")
V[("走破", "競馬場も揃えて標準化・近3走の平均")] = roll("spd_zB", 3, "mean")
V[("走破", "競馬場も揃えて標準化・指数平滑3")] = ewm("spd_zB", 3)

V[("上がり", "現行: 近5走の平均 (レース平均との差)")] = -roll("agari_dev", 5, "mean")
V[("上がり", "近5走の最良")] = -roll("agari_dev", 5, "min")
V[("上がり", "近3走の平均")] = -roll("agari_dev", 3, "mean")
V[("上がり", "近5走のレース内順位の平均")] = -roll("agari_rank", 5, "mean")
V[("上がり", "レース内順位・指数平滑3")] = -ewm("agari_rank", 3)

V[("近走", "現行: 近3走の着順位置の平均")] = -roll("fin_norm", 3, "mean")
V[("近走", "前走の着順位置")] = -roll("fin_norm", 1, "mean")
V[("近走", "近5走の着順位置の平均")] = -roll("fin_norm", 5, "mean")
V[("近走", "着順位置・指数平滑3")] = -ewm("fin_norm", 3)
V[("近走", "近3走の勝ち馬とのタイム差の平均")] = -roll("behind", 3, "mean")
V[("近走", "近3走の勝ち馬とのタイム差の最小")] = -roll("behind", 3, "min")
V[("近走", "勝ち馬とのタイム差・指数平滑3")] = -ewm("behind", 3)

V[("着順", "現行: 通算複勝率 (縮め3走)")] = cum_rate(["horse_id"], 3)
V[("着順", "通算複勝率 (縮め1走)")] = cum_rate(["horse_id"], 1)
V[("着順", "通算複勝率 (縮め10走)")] = cum_rate(["horse_id"], 10)
V[("着順", "通算の着順位置の平均")] = -roll("fin_norm", 200, "mean")
V[("着順", "通算のタイム差の平均")] = -roll("behind", 200, "mean")

V[("距離", "現行: 400m刻みの距離帯の複勝率 (縮め3)")] = cum_rate(["horse_id", "dist_band"], 3)
V[("距離", "200m刻みの複勝率 (縮め3)")] = cum_rate(["horse_id", "dist200"], 3)
V[("距離", "400m刻み・芝ダ別 (縮め3)")] = cum_rate(["horse_id", "surface", "dist_band"], 3)
V[("距離", "400m刻み (縮め10)")] = cum_rate(["horse_id", "dist_band"], 10)

V[("コース", "現行: 同じ競馬場の複勝率 (縮め3)")] = cum_rate(["horse_id", "place_cd"], 3)
V[("コース", "同じ競馬場・芝ダ (縮め3)")] = cum_rate(["horse_id", "place_cd", "surface"], 3)
V[("コース", "同じ競馬場 (縮め10)")] = cum_rate(["horse_id", "place_cd"], 10)

V[("騎手", "現行: 通算 (縮め30)")] = cum_rate(["jockey_id"], 30, 10)
V[("騎手", "直近1年 (縮め30)")] = cum_rate(["jockey_id"], 30, 10, days=365)
V[("騎手", "直近90日 (縮め30)")] = cum_rate(["jockey_id"], 30, 10, days=90)
V[("騎手", "その競馬場で (縮め30)")] = cum_rate(["jockey_id", "place_cd"], 30, 10)
V[("騎手", "芝ダ別・直近1年 (縮め30)")] = cum_rate(["jockey_id", "surface"], 30, 10, days=365)

V[("厩舎", "現行: 通算 (縮め30)")] = cum_rate(["trainer_id"], 30, 10)
V[("厩舎", "直近1年 (縮め30)")] = cum_rate(["trainer_id"], 30, 10, days=365)
V[("厩舎", "直近90日 (縮め30)")] = cum_rate(["trainer_id"], 30, 10, days=90)
V[("厩舎", "芝ダ別 (縮め30)")] = cum_rate(["trainer_id", "surface"], 30, 10)

V[("血統", "現行相当: 父の産駒 通算 (縮め30)")] = cum_rate(["sire"], 30)
V[("血統", "父×芝ダ (縮め30)")] = cum_rate(["sire", "surface"], 30)
V[("血統", "父×距離帯 (縮め30)")] = cum_rate(["sire", "dist_band"], 30)
V[("血統", "父×芝ダ×距離帯 (縮め30)")] = cum_rate(["sire", "surface", "dist_band"], 30)
V[("血統", "父×芝ダ×馬場 (縮め30)")] = cum_rate(["sire", "surface", "going_grp"], 30)

V[("馬場", "現行: 良/道悪の複勝率 (縮め3)")] = cum_rate(["horse_id", "going_grp"], 3)
V[("馬場", "4段階の馬場の複勝率 (縮め3)")] = cum_rate(["horse_id", "going"], 3)
V[("馬場", "良/道悪・芝ダ別 (縮め3)")] = cum_rate(["horse_id", "surface", "going_grp"], 3)
car = cum_rate(["horse_id"], 3)
V[("馬場", "通算との差 (この馬場が得意か)")] = cum_rate(["horse_id", "going_grp"], 3) - car
print(f"候補 {len(V)}件を計算 {time.time() - T0:.0f}s")

A = (d["date"] >= "2025-01-01") & (d["date"] < "2026-05-19")
B = d["date"] >= "2026-05-19"


def evaluate(score, mask):
    s = score[mask]
    s = s.fillna(s.median())  # データ無しは真ん中 (サイトで50と表示するのと同じ)
    x = pd.DataFrame({"race": d.loc[mask, "race_id"], "s": s, "y": d.loc[mask, "_top3"]})
    x["r"] = x.groupby("race")["s"].rank(method="average")
    g = x.groupby("race")
    npos, n = g["y"].sum(), g["y"].count()
    nneg = n - npos
    spos = (x["r"] * x["y"]).groupby(x["race"]).sum()
    ok = (npos > 0) & (nneg > 0)
    auc = ((spos - npos * (npos + 1) / 2) / (npos * nneg))[ok].mean()
    top = x.sort_values("s", ascending=False).groupby("race").head(1)
    cover = score[mask].notna().mean()
    return auc, top["y"].mean(), cover


rows = []
for (comp, name), score in V.items():
    a = evaluate(score, A)
    b = evaluate(score, B)
    rows.append({"項目": comp, "方法": name, "AUC_A": a[0], "AUC_B": b[0],
                 "首位複勝率_B": b[1], "データあり": b[2]})
res = pd.DataFrame(rows)
pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 40)
out = []
for comp, g in res.groupby("項目", sort=False):
    best = g["AUC_A"].idxmax()
    for i, r in g.iterrows():
        out.append({**r.to_dict(), "採用": "◎" if i == best else ""})
res = pd.DataFrame(out)
for c in ("AUC_A", "AUC_B", "首位複勝率_B", "データあり"):
    res[c] = res[c].map(lambda v: f"{v:.3f}")
print(res.to_string(index=False))
res.to_csv(ROOT + r"\data\refine_components.csv", index=False, encoding="utf-8-sig")

# 採用案をまとめて保存 (次の段階で指数の作り直しに使う)
chosen = {}
for comp, g in pd.DataFrame(rows).groupby("項目", sort=False):
    best = g.loc[g["AUC_A"].idxmax()]
    chosen[comp] = V[(comp, best["方法"])]
pd.DataFrame(chosen).assign(race_id=d["race_id"], horse_no=d["horse_no"], date=d["date"],
                            top3=d["_top3"]).to_pickle(ROOT + r"\data\refine_chosen.pkl")
print(f"完了 {time.time() - T0:.0f}s")
