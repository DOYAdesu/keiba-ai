"""回顧 (前走の上がり順位・着差・流れの有利不利) を特徴量に足すと当たるようになるかを比べる。

同じ確認期間 (2026-05-19〜、中央のみ) で、今の特徴量だけのモデルと、回顧の特徴量を足したモデルを比べる。
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from keiba.features import FEATURES, build_training_data, load_horses, load_results  # noqa: E402
from keiba.review_feats import REVIEW_FEATURES, add_review_features  # noqa: E402
from keiba.train import _fit_lgbm  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
SPLIT = pd.Timestamp("2026-05-19")
START = pd.Timestamp("2020-01-01")
T0 = time.time()
df = load_results()
X, meta = build_training_data(df, load_horses())
R = add_review_features(meta)
print(f"特徴量 {time.time() - T0:.0f}s / {len(X)}行")
print(R.describe().T.round(3).to_string())

valid = (meta["date"] >= SPLIT).to_numpy()
vj = valid & (meta["is_nar"] == 0).to_numpy()
tr = ((meta["date"] >= START) & (meta["date"] < SPLIT)).to_numpy()


def race_auc(race, s, y):
    x = pd.DataFrame({"race": race, "s": s, "y": y})
    x["r"] = x.groupby("race")["s"].rank(method="average")
    g = x.groupby("race")
    npos, n = g["y"].sum(), g["y"].count()
    nneg = n - npos
    spos = (x["r"] * x["y"]).groupby(x["race"]).sum()
    ok = (npos > 0) & (nneg > 0)
    auc = ((spos - npos * (npos + 1) / 2) / (npos * nneg))[ok].mean()
    top = x.sort_values("s", ascending=False).groupby("race").head(1)
    return auc, top["y"].mean()


rows = []
for name, XX in (("今の特徴量", X), ("＋回顧", pd.concat([X, R], axis=1))):
    m = _fit_lgbm(XX[tr], meta["top3"][tr], XX[valid], meta["top3"][valid])
    p = m.predict_proba(XX[vj])[:, 1]
    auc, top1 = race_auc(meta["race_id"][vj].values, p, meta["top3"][vj].values)
    mw = _fit_lgbm(XX[tr], meta["win"][tr], XX[valid], meta["win"][valid])
    pw = mw.predict_proba(XX[vj])[:, 1]
    aucw, top1w = race_auc(meta["race_id"][vj].values, pw,
                           (meta["finish"][vj] == 1).astype(float).values)
    rows.append({"特徴量": name, "AUC複勝": round(auc, 4), "1位の複勝率": round(top1, 4),
                 "AUC単勝": round(aucw, 4), "1位の勝率": round(top1w, 4)})
    print(rows[-1], f"{time.time() - T0:.0f}s", flush=True)
    if name != "今の特徴量":
        imp = sorted(zip(XX.columns, m.feature_importances_), key=lambda t: -t[1])
        print("重要度:", [(k, int(v)) for k, v in imp if k in REVIEW_FEATURES])
print(pd.DataFrame(rows).to_string(index=False))
