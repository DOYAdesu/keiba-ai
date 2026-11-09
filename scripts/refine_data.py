"""学習に使う期間を変えると当たり方がどう変わるかを比べる。

同じ確認期間 (2026-05-19〜、中央のみ) で、学習の開始年だけを変えたモデルを比べる。
評価: レース内AUC (3着以内) と、モデル1位の馬の複勝率。
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from keiba.features import build_training_data, load_horses, load_results  # noqa: E402
from keiba.train import _fit_lgbm  # noqa: E402

SPLIT = pd.Timestamp("2026-05-19")
T0 = time.time()
df = load_results()
X, meta = build_training_data(df, load_horses())
print(f"特徴量 {time.time() - T0:.0f}s / {len(X)}行")

valid = (meta["date"] >= SPLIT).to_numpy()
vj = valid & (meta["is_nar"] == 0).to_numpy()


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
for start in sys.argv[1:] or ["2025-01-01", "2023-01-01", "2020-01-01"]:
    tr = ((meta["date"] >= pd.Timestamp(start)) & (meta["date"] < SPLIT)).to_numpy()
    m = _fit_lgbm(X[tr], meta["top3"][tr], X[valid], meta["top3"][valid])
    p = m.predict_proba(X[vj])[:, 1]
    auc, top1 = race_auc(meta["race_id"][vj].values, p, meta["top3"][vj].values)
    mw = _fit_lgbm(X[tr], meta["win"][tr], X[valid], meta["win"][valid])
    pw = mw.predict_proba(X[vj])[:, 1]
    aucw, top1w = race_auc(meta["race_id"][vj].values, pw,
                           (meta["finish"][vj] == 1).astype(float).values)
    rows.append({"学習開始": start, "学習行数": int(tr.sum()),
                 "AUC複勝": round(auc, 4), "1位の複勝率": round(top1, 4),
                 "AUC単勝": round(aucw, 4), "1位の勝率": round(top1w, 4)})
    print(rows[-1], f"{time.time() - T0:.0f}s", flush=True)

print(pd.DataFrame(rows).to_string(index=False))
