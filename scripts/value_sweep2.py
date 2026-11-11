import sys; from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from keiba import value as V
h = pd.read_pickle(ROOT / "data" / "value_hist.pkl")
X, meta = pd.read_pickle(ROOT / "data" / "refine_cache.pkl")
EXTRA = ["prev_agari_rank_r", "prev_to_win", "prev_flow_bad", "prev_pos_late", "avg_agari_rank_r5",
         "days_since_last", "weight_diff", "jockey_top3_rate", "career_top3_rate", "last_finish",
         "workout_lap1f", "dist_change", "kinryo"]
xx = X[EXTRA].copy(); xx["race_id"] = meta["race_id"].values; xx["horse_no"] = meta["horse_no"].values
h = h.merge(xx, on=["race_id", "horse_no"], how="left")
# レースの中で標準化 (市場との相対)
for c in EXTRA:
    g = h.groupby("race_id")[c]
    z = (h[c] - g.transform("mean")) / g.transform("std").replace(0, np.nan)
    h["z_" + c] = z.fillna(0).clip(-3, 3)
base = list(V.FEATS)
def run(feats, tag):
    V.FEATS = feats
    preds = []
    for y in range(2022, 2027):
        tr, te = h[h["year"] < y], h[h["year"] == y].copy()
        te["p"] = V.predict(te, V.fit(tr, l2=3.0)); preds.append(te)
    P = pd.concat(preds)
    ll = -np.log(np.clip(P.p[P.win == 1], 1e-6, 1)).mean()
    print(f"[{tag}] 対数尤度 {ll:.4f} (市場 {-np.log(P.pm[P.win == 1]).mean():.4f})")
    for ev, om in ((1.0, 10), (1.0, 30), (1.05, 30), (1.1, 30)):
        b = P[(P.p * P.o5 >= ev) & (P.o5 <= om)]
        r = np.where(b.win == 1, b.odds, 0)
        yr = b.assign(r=r).groupby("year")["r"].mean().round(2).to_dict()
        print(f"  EV>={ev} odds<={om}: {len(b)}点 回収 {r.mean():.3f} ±{2*r.std()/np.sqrt(max(len(b),1)):.3f} {yr}")
    return P
run(base, "直前+馬場")
P = run(base + ["z_" + c for c in EXTRA], "＋近走・騎手など")
V.FEATS = base + ["z_" + c for c in EXTRA]
coef = V.fit(h, l2=3.0)
print({k: round(v, 3) for k, v in coef.items()})
