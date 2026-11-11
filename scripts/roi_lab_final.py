"""最終候補5つを、選定に使っていない 2023〜2026 で1回だけ確かめる。

候補は開発期間 (2020-22) の結果だけで次のとおり決めた (この後は変えない):
  W0 今のモデル EV>=1.0, 30倍以下
  W1 3モデルすべて EV>=1.0, 30倍以下
  W2 3モデルの平均 EV>=1.1, 30倍以下
  P1 複勝 市場+単複+動き EV>=1.1
  P2 複勝 +脚質+近走+AI EV>=1.2
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from roi_lab import *  # noqa
import lightgbm as lgb
from sklearn.linear_model import LogisticRegression

def report(nm, b, col):
    b = b.copy(); b["ret"] = b[col]; b["stake"] = 1.0
    if len(b) < 30:
        print(f"{nm}: {len(b)}点 (少なすぎ)"); return
    d = b.groupby("date").agg(r=("ret", "sum"), s=("stake", "sum"))
    rng = np.random.default_rng(0); i = rng.integers(0, len(d), (5000, len(d)))
    v = d["r"].to_numpy()[i].sum(1) / d["s"].to_numpy()[i].sum(1)
    yr = "  ".join(f"{y}:{g['ret'].mean():.0%}({len(g)})" for y, g in b.groupby("year"))
    print(f"{nm:22s} {len(b):5d}点 回収 {b['ret'].mean():6.1%}  95%[{np.percentile(v, 2.5):.1%},{np.percentile(v, 97.5):.1%}]"
          f"  99%[{np.percentile(v, .5):.1%},{np.percentile(v, 99.5):.1%}]  100%超の確率 {(v > 1).mean():.0%} | {yr}", flush=True)

for tag in ("t5", "t10"):
    print(f"\n===== 判断の時点 {tag} / 確認期間 2023-2026", flush=True)
    h = load(tag)
    fe = [c for c in h.columns if c.startswith("zx_")] + ["mv5", "mv_pre", "style", "front_bias", "back_bias", "ai_gap", "ai3_gap", "pl_gap", "mv_late", "lpm"]
    out = []
    for y in HOLD:
        tr, te = h[(h["year"] < y) & (h["year"] >= 2018)], h[h["year"] == y].copy()
        off = lambda d: np.log(d["pm"] / (1 - d["pm"]).clip(1e-6))
        m = lgb.train(dict(objective="binary", learning_rate=.02, num_leaves=4, min_data_in_leaf=500, feature_fraction=.5,
                           bagging_fraction=.7, bagging_freq=1, lambda_l2=50, verbose=-1, seed=1),
                      lgb.Dataset(tr[fe], tr["win"], init_score=off(tr)), 300)
        z = off(te) + m.predict(te[fe], raw_score=True)
        p = pd.Series(1 / (1 + np.exp(-z)), index=te.index)
        te["pc"] = p / p.groupby(te["race_id"]).transform("sum")
        out.append(te)
    C = pd.concat(out)
    a = wf(h, BASE, HOLD, first=2018); b = wf(h, BASE + ["ai_gap", "lpm2"], HOLD, first=2018)
    key = ["race_id", "horse_no"]
    M = C.merge(a[key + ["p"]].rename(columns={"p": "pa"}), on=key).merge(b[key + ["p"]].rename(columns={"p": "pb"}), on=key)
    M["tan"] = np.where(M["win"] == 1, M["odds"], 0.0)
    ok = M["o5"] <= 30
    report("W0 今のモデル", M[ok & (M["pa"] * M["o5"] >= 1)], "tan")
    report("W1 3つとも", M[ok & (M["pa"] * M["o5"] >= 1) & (M["pb"] * M["o5"] >= 1) & (M["pc"] * M["o5"] >= 1)], "tan")
    e = np.exp((np.log(M["pa"]) + np.log(M["pb"]) + np.log(M["pc"])) / 3)
    M["pe"] = e / e.groupby(M["race_id"]).transform("sum")
    report("W2 平均 EV1.1", M[ok & (M["pe"] * M["o5"] >= 1.1)], "tan")
    print(f"  (参考) 全頭の単勝回収 {M['tan'].mean():.1%}")
    P = h[h["fs"] >= 8].copy()
    lo, hi = P[f"plo_{tag}"], P[f"phi_{tag}"]
    P = P[lo.notna() & hi.notna() & (lo > 0)]
    mid = (P[f"plo_{tag}"] + P[f"phi_{tag}"]) / 2
    ip = 1 / mid; P["ip3"] = (ip / ip.groupby(P["race_id"]).transform("sum") * 3).clip(.005, .97)
    P["lip"] = np.log(P["ip3"] / (1 - P["ip3"])); P["top3"] = (P["finish"] <= 3).astype(int)
    P["fuku"] = P["pay"].fillna(0) / 100
    ext = ["z_" + c for c in value.EXTRA]
    f1 = ["lip", "lpm", "lpm2", "pl_gap", "mv5", "mv_pre"]
    f2 = f1 + ["style", "front_bias", "back_bias", "ai_gap", "ai3_gap"] + ext
    for nm, f, thr in (("P1 複勝 動き EV1.1", f1, 1.1), ("P2 複勝 AI EV1.2", f2, 1.2)):
        out = []
        for y in HOLD:
            tr, te = P[(P["year"] < y) & (P["year"] >= 2018)], P[P["year"] == y].copy()
            mm = LogisticRegression(C=.1, max_iter=500).fit(tr[f], tr["top3"])
            te["ev"] = mm.predict_proba(te[f])[:, 1] * (te[f"plo_{tag}"] + .25 * (te[f"phi_{tag}"] - te[f"plo_{tag}"]))
            out.append(te)
        T = pd.concat(out)
        report(nm, T[T["ev"] >= thr], "fuku")
    print(f"  (参考) 全頭の複勝回収 {T['fuku'].mean():.1%}")
