import sys; from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from keiba import value as V
h = V.build_history()
h.to_pickle(Path(__file__).resolve().parents[1] / "data" / "value_hist.pkl")
preds = []
for y in range(2022, 2027):
    tr, te = h[h["year"] < y], h[h["year"] == y].copy()
    te["p"] = V.predict(te, V.fit(tr))
    preds.append(te)
P = pd.concat(preds)
print("全頭", round(float(np.where(P.win == 1, P.odds, 0).mean()), 3))
for ev in (1.0, 1.05, 1.1, 1.2, 1.3):
    for om in (10, 20, 30, 999):
        b = P[(P.p * P.o5 >= ev) & (P.o5 <= om)]
        if len(b) < 50: continue
        r = np.where(b.win == 1, b.odds, 0)
        yr = b.assign(r=r).groupby("year")["r"].mean().round(2).to_dict()
        print(f"EV>={ev} odds<={om}: {len(b)}点 回収 {r.mean():.3f} ±{2*r.std()/np.sqrt(len(b)):.3f} 年別 {yr}")
# 市場どおり (補正なし) と比べた的中の当てはまり
def ll(p): return -np.log(np.clip(p[P.win == 1], 1e-6, 1)).mean()
print("対数尤度 (勝ち馬) 市場", round(ll(P.pm), 4), "モデル", round(ll(P.p), 4))
