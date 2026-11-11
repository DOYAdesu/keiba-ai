"""回収率の実験用データを作る (data/roi_lab.pkl)。

1頭1行に、発走前の各時点のオッズ・複勝オッズ・全特徴量・AIの予測を付ける。
AIの予測は「その年より前だけで学習して、その年を予測」したもの (未来のデータは入らない)。
"""
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from keiba import value  # noqa: E402
from keiba.features import FEATURES, build_training_data, load_horses, load_results  # noqa: E402
from keiba.scraper import DB_PATH  # noqa: E402
from keiba.train import _fit_lgbm  # noqa: E402

T0 = time.time()
say = lambda m: print(f"[{time.time() - T0:5.0f}s] {m}", flush=True)

X, meta = build_training_data(load_results(), load_horses())
X, meta = X.reset_index(drop=True), meta.reset_index(drop=True)
pd.to_pickle((X, meta), ROOT / "data" / "refine_cache.pkl")   # 2016年からの特徴量でキャッシュを作り直す
say(f"特徴量 {len(X)}行 {meta['date'].min().date()}〜")

value.FIRST_YEAR = 2016
h = value.build_history()
say(f"履歴 {h['race_id'].nunique()}レース {len(h)}頭")

conn = sqlite3.connect(DB_PATH)
for tag in ("t20", "t10", "t5"):
    recs = []
    for rid, win, plo, phi in conn.execute(
            "SELECT race_id, win, plo, phi FROM odds_hist WHERE tag=? AND win IS NOT NULL", (tag,)):
        w, a, b = win.split(","), (plo or "").split(","), (phi or "").split(",")
        for i, v in enumerate(w):
            f = lambda L: float(L[i]) if i < len(L) and L[i] else np.nan
            recs.append((rid, i + 1, float(v) if v else np.nan, f(a), f(b)))
    t = pd.DataFrame(recs, columns=["race_id", "horse_no", f"w_{tag}", f"plo_{tag}", f"phi_{tag}"])
    h = h.merge(t, on=["race_id", "horse_no"], how="left")
grade = pd.read_sql("SELECT race_id, grade FROM races", conn)
conn.close()
h = h.merge(grade, on="race_id", how="left")

xs = X[FEATURES].add_prefix("x_")
xs["race_id"], xs["horse_no"] = meta["race_id"].values, pd.to_numeric(meta["horse_no"], errors="coerce").values
xs["distance_m"], xs["race_name"] = meta["distance"].values, meta["race_name"].values
h = h.merge(xs, on=["race_id", "horse_no"], how="left")

jra = (meta["is_nar"] == 0).to_numpy()
year = meta["date"].dt.year.to_numpy()
oof = {k: np.full(len(X), np.nan) for k in ("win", "top3")}
for y in range(2018, int(year.max()) + 1):
    start = pd.Timestamp(f"{y}-01-01")
    before = jra & (meta["date"] < start).to_numpy()
    va = before & (meta["date"] >= start - pd.Timedelta(days=180)).to_numpy()
    tr = before & ~va
    te = jra & (year == y)
    for k in oof:
        m = _fit_lgbm(X[tr], meta[k][tr], X[va], meta[k][va])
        oof[k][te] = m.predict_proba(X[te])[:, 1]
    say(f"AI {y} 学習 {tr.sum()}行 → {te.sum()}行")
a = pd.DataFrame({"race_id": meta["race_id"].values,
                  "horse_no": pd.to_numeric(meta["horse_no"], errors="coerce").values,
                  "ai_win": oof["win"], "ai_top3": oof["top3"]})
h = h.merge(a, on=["race_id", "horse_no"], how="left")
pd.to_pickle(h, ROOT / "data" / "roi_lab.pkl")
say(f"保存 {h.shape}  {h['date'].min()}〜{h['date'].max()}")
print(h.groupby("year").agg(races=("race_id", "nunique"), ai=("ai_win", lambda s: s.notna().mean()),
                            plo=("plo_t5", lambda s: s.notna().mean()), pay=("pay", lambda s: s.notna().mean())))
