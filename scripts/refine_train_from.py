"""学習に使う行を 2016年〜 にするか 2020年〜 のままにするかを比べる。

同じ確認期間 (直近180日、中央のみ) で、レース内AUC・1位の的中率を比べる。
特徴量 (通算成績など) はどちらも全期間の成績から作る。
"""
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from keiba.features import build_training_data, load_horses, load_results  # noqa: E402
from keiba.oddsmove import model_odds  # noqa: E402
from keiba.features import add_market_features  # noqa: E402
from keiba.train import VALID_DAYS, _fit_lgbm  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from refine_live import race_auc  # noqa: E402

T0 = time.time()
X, meta = build_training_data(load_results(), load_horses())
meta = meta.reset_index(drop=True)
X = X.reset_index(drop=True)
print(f"特徴量 {time.time() - T0:.0f}s / {len(X)}行 ({meta['date'].min().date()}〜)", flush=True)
split = meta["date"].max() - pd.Timedelta(days=VALID_DAYS)
valid = (meta["date"] > split).to_numpy()
vj = valid & (meta["is_nar"] == 0).to_numpy()
Xm = add_market_features(X, model_odds(meta), meta["race_id"])
rows = []
for start in ("2016-01-01", "2018-01-01", "2020-01-01"):
    tr = ((meta["date"] >= pd.Timestamp(start)) & (meta["date"] <= split)).to_numpy()
    if not tr.any():
        continue
    for name, XX in (("能力", X), ("市場(40分前)", Xm)):
        for target in ("top3", "win"):
            m = _fit_lgbm(XX[tr], meta[target][tr], XX[valid], meta[target][valid])
            p = m.predict_proba(XX[vj])[:, 1]
            auc, top1 = race_auc(meta["race_id"][vj].values, p, meta[target][vj].values)
            rows.append({"学習の始まり": start[:4], "モデル": name, "対象": target, "学習行数": int(tr.sum()),
                         "AUC": round(auc, 4), "1位の的中率": round(top1, 4)})
            print(rows[-1], f"{time.time() - T0:.0f}s", flush=True)
print(pd.DataFrame(rows).to_string(index=False))
