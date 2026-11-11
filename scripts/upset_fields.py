"""出走馬の顔ぶれ (成績・タイム) で、100万超の出やすさがどう変わるかを調べる。

レースの条件 (頭数・距離など) に加えて、出走前に分かる馬の情報だけで区切る:
  AIの能力指数が高い馬の頭数 / 走破タイムの上位の差 / 前走3着内の馬の数 /
  逃げ馬の数 / 休み明けの馬 / 40分前の1番人気のオッズ
さらに、今の荒れ度の推定 (p_hit_1m) にこれらを足すと当たり方が良くなるかも確かめる。
"""
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from keiba.scraper import DB_PATH  # noqa: E402

conn = sqlite3.connect(DB_PATH)
X, meta = pd.read_pickle(ROOT / "data" / "refine_cache.pkl")
h = meta[["race_id", "horse_no", "date"]].copy()
for c in ("last_finish", "days_since_last", "career_starts", "avg_corner1_ratio5", "field_size"):
    h[c] = X[c].values
sp = pd.read_sql("SELECT race_id, horse_no, ability_idx, c_spd, p_win FROM site_preds", conn)
h = h.merge(sp, on=["race_id", "horse_no"], how="inner")
races = pd.read_sql("SELECT race_id, date, field_size, surface, p_hit_1m, in_sample FROM site_races "
                    "WHERE is_nar=0", conn)
pay = pd.read_sql("SELECT race_id, MAX(pay) pay FROM payouts WHERE bet_type='三連単' GROUP BY race_id", conn)
races = races.merge(pay, on="race_id")
races["hit"] = (races["pay"] >= 1_000_000).astype(float)

from keiba.oddsmove import model_odds  # noqa: E402
h["odds40"] = model_odds(h.assign(odds=np.nan)).values


def per_race(g):
    spd = g["c_spd"].dropna().sort_values(ascending=False)
    o = g["odds40"].dropna().sort_values()
    return pd.Series({
        "n_strong": int((g["ability_idx"] >= 80).sum()),
        "spd_gap": (spd.iloc[0] - spd.iloc[2]) if len(spd) >= 3 else np.nan,
        "n_good_last": int((g["last_finish"] <= 3).sum()),
        "n_nige": int((g["avg_corner1_ratio5"] <= 0.15).sum()),
        "n_layoff": int((g["days_since_last"] >= 180).sum()),
        "n_new": int((g["career_starts"] == 0).sum()),
        "fav": o.iloc[0] if len(o) else np.nan,
        "top_ai": g["p_win"].max(),
    })


f = h.groupby("race_id").apply(per_race)
d = races.merge(f, left_on="race_id", right_index=True)
d = d[d["surface"] != "障"]
base = d["hit"].mean()
print(f"{len(d)}レース 全体 {base:.2%}")

axes = {
    "能力指数80以上の頭数": pd.cut(d["n_strong"], [-1, 1, 3, 5, 99], labels=["0-1頭", "2-3頭", "4-5頭", "6頭以上"]),
    "走破タイム1位と3位の差": pd.qcut(d["spd_gap"], 4, labels=["拮抗(下位25%)", "やや拮抗", "やや差", "抜けた馬あり(上位25%)"]),
    "前走3着内の馬": pd.cut(d["n_good_last"], [-1, 2, 4, 6, 99], labels=["0-2頭", "3-4頭", "5-6頭", "7頭以上"]),
    "逃げ馬": pd.cut(d["n_nige"], [-1, 0, 1, 2, 99], labels=["0頭", "1頭", "2頭", "3頭以上"]),
    "半年以上の休み明け": pd.cut(d["n_layoff"], [-1, 0, 2, 99], labels=["0頭", "1-2頭", "3頭以上"]),
    "40分前の1番人気": pd.cut(d["fav"], [0, 1.99, 2.99, 3.99, 999], labels=["2倍未満", "2倍台", "3倍台", "4倍以上"]),
    "AIの本命の勝率": pd.cut(d["top_ai"], [0, 0.15, 0.25, 0.4, 1], labels=["15%未満", "15-25%", "25-40%", "40%以上"]),
}
for name, key in axes.items():
    t = d.groupby(key, observed=True)["hit"].agg(["mean", "size"])
    print(f"\n{name}")
    for k, r in t.iterrows():
        print(f"  {k}: {r['mean']:.2%} ({int(r['size'])}R) {r['mean'] / base:.2f}倍")

# 今の荒れ度に足すと良くなるか (確認期間)
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import log_loss, roc_auc_score  # noqa: E402

d = d.dropna(subset=["p_hit_1m", "spd_gap", "fav"])
lp = np.log(d["p_hit_1m"].clip(1e-4, 0.5) / (1 - d["p_hit_1m"].clip(1e-4, 0.5)))
feats = pd.DataFrame({"lp": lp, "n_strong": d["n_strong"], "spd_gap": d["spd_gap"],
                      "n_good_last": d["n_good_last"], "n_nige": d["n_nige"], "n_layoff": d["n_layoff"],
                      "fs": d["field_size"]})
tr = (d["date"] < "2025-11-12").to_numpy()
va = (d["date"] >= "2026-05-12").to_numpy()
y = d["hit"].to_numpy()
for name, cols in (("今の荒れ度だけ", ["lp"]), ("＋顔ぶれ", list(feats.columns))):
    m = LogisticRegression(max_iter=1000).fit(feats.loc[tr, cols], y[tr])
    p = m.predict_proba(feats.loc[va, cols])[:, 1]
    print(f"{name}: AUC {roc_auc_score(y[va], p):.4f} logloss {log_loss(y[va], p):.4f} "
          f"(確認期間 {va.sum()}R・100万超 {int(y[va].sum())}件)")
    if name != "今の荒れ度だけ":
        print("  係数:", dict(zip(cols, np.round(m.coef_[0], 3))))

# 頭数をそろえて見る (16頭以上だけ)
big = d[d["field_size"] >= 16]
print(f"\n16頭以上だけ ({len(big)}R, 全体 {big['hit'].mean():.2%})")
for name, col, bins, labels in (
        ("能力指数80以上", "n_strong", [-1, 1, 3, 99], ["0-1頭", "2-3頭", "4頭以上"]),
        ("前走3着内", "n_good_last", [-1, 3, 5, 99], ["0-3頭", "4-5頭", "6頭以上"]),
        ("逃げ馬", "n_nige", [-1, 0, 1, 99], ["0頭", "1頭", "2頭以上"]),
        ("休み明け", "n_layoff", [-1, 0, 99], ["0頭", "1頭以上"]),
        ("タイム差", "spd_gap", [-1, d["spd_gap"].quantile(.25), d["spd_gap"].quantile(.75), 999], ["拮抗", "中", "抜けた馬"])):
    t = big.groupby(pd.cut(big[col], bins, labels=labels), observed=True)["hit"].agg(["mean", "size"])
    print(f"  {name}: " + " / ".join(f"{k} {r['mean']:.2%}({int(r['size'])})" for k, r in t.iterrows()))

# 長い期間で確かめる: 2020-2023で学習 → 2024年以降で確認
tr2 = (d["date"] < "2024-01-01").to_numpy()
va2 = (d["date"] >= "2024-01-01").to_numpy()
for name, cols in (("今の荒れ度だけ", ["lp"]), ("＋顔ぶれ", list(feats.columns))):
    m = LogisticRegression(max_iter=1000).fit(feats.loc[tr2, cols], y[tr2])
    p = m.predict_proba(feats.loc[va2, cols])[:, 1]
    q = pd.qcut(p, 5, labels=False, duplicates="drop")
    print(f"[2024〜] {name}: AUC {roc_auc_score(y[va2], p):.4f} logloss {log_loss(y[va2], p):.4f} "
          f"({va2.sum()}R・{int(y[va2].sum())}件) 上位20%の発生率 {y[va2][q == q.max()].mean():.2%}")
