"""第3弾: 複数モデルの平均・意見が一致した馬・複勝モデルの作り込み (開発期間 2020-22 のみ)。"""
import sys, time
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from roi_lab import *  # noqa
import lightgbm as lgb
from sklearn.linear_model import LogisticRegression
T0 = time.time()
h = load("t5")
F = lambda *a: BASE + list(a)
fe = [c for c in h.columns if c.startswith("zx_")] + ["mv5", "mv_pre", "style", "front_bias", "back_bias", "ai_gap", "ai3_gap", "pl_gap", "mv_late", "lpm"]
def gbm(leaves=4, n=300):
    out = []
    for y in DEV:
        tr, te = h[(h["year"] < y) & (h["year"] >= 2018)], h[h["year"] == y].copy()
        off = lambda d: np.log(d["pm"] / (1 - d["pm"]).clip(1e-6))
        m = lgb.train(dict(objective="binary", learning_rate=.02, num_leaves=leaves, min_data_in_leaf=500, feature_fraction=.5,
                           bagging_fraction=.7, bagging_freq=1, lambda_l2=50, verbose=-1, seed=1),
                      lgb.Dataset(tr[fe], tr["win"], init_score=off(tr)), n)
        z = off(te) + m.predict(te[fe], raw_score=True)
        p = pd.Series(1 / (1 + np.exp(-z)), index=te.index)
        te["p"] = p / p.groupby(te["race_id"]).transform("sum")
        out.append(te)
    return pd.concat(out)
a = wf(h, BASE, DEV, first=2018); b = wf(h, F("ai_gap", "lpm2"), DEV, first=2018); c = gbm()
key = ["race_id", "horse_no"]
m = a[key + ["year", "date", "o5", "odds", "win", "pm", "ai_gap", "p"]].rename(columns={"p": "pa"})
m = m.merge(b[key + ["p"]].rename(columns={"p": "pb"}), on=key).merge(c[key + ["p"]].rename(columns={"p": "pc"}), on=key)
print("--- 勝率モデルの平均 / 一致", flush=True)
z = (np.log(m["pa"]) + np.log(m["pb"]) + np.log(m["pc"])) / 3
e = np.exp(z); m["p"] = e / e.groupby(m["race_id"]).transform("sum")
line("3モデルの平均", m)
for ev in (1.05, 1.1, 1.2):
    r = roi(m, ev, 30); print(f"  平均 EV≥{ev}: {len(r)}点 {r['ret'].sum() / max(len(r), 1):.1%}")
for ev in (1.0, 1.05, 1.1):
    mask = (m["pa"] * m["o5"] >= ev) & (m["pb"] * m["o5"] >= ev) & (m["pc"] * m["o5"] >= ev) & (m["o5"] <= 30)
    r = m[mask]; ret = np.where(r["win"] == 1, r["odds"], 0)
    print(f"  3つとも EV≥{ev}: {len(r)}点 回収 {ret.mean():.1%}", flush=True)
print("--- 複勝モデル", flush=True)
P = h[h["fs"] >= 8].copy()
mid = (P["plo_t5"] + P["phi_t5"]) / 2
P = P[mid.notna()]; mid = mid[P.index]
ip = 1 / mid; P["ip3"] = (ip / ip.groupby(P["race_id"]).transform("sum") * 3).clip(.005, .97)
P["lip"] = np.log(P["ip3"] / (1 - P["ip3"])); P["top3"] = (P["finish"] <= 3).astype(int)
P["pay_r"] = P["pay"].fillna(0) / 100
ext = ["z_" + c for c in value.EXTRA]
sets = {"市場だけ": ["lip"], "市場+単複": ["lip", "lpm", "lpm2", "pl_gap"],
        "+動き": ["lip", "lpm", "lpm2", "pl_gap", "mv5", "mv_pre"],
        "+脚質馬場": ["lip", "lpm", "lpm2", "pl_gap", "mv5", "mv_pre", "style", "front_bias", "back_bias"],
        "+近走": ["lip", "lpm", "lpm2", "pl_gap", "mv5", "mv_pre", "style", "front_bias", "back_bias"] + ext,
        "+AI": ["lip", "lpm", "lpm2", "pl_gap", "mv5", "mv_pre", "style", "front_bias", "back_bias", "ai_gap", "ai3_gap"] + ext}
def place(feats, C=.1):
    out = []
    for y in DEV:
        tr, te = P[(P["year"] < y) & (P["year"] >= 2018)], P[P["year"] == y].copy()
        mm = LogisticRegression(C=C, max_iter=500).fit(tr[feats], tr["top3"])
        te["p3"] = mm.predict_proba(te[feats])[:, 1]
        te["ex"] = te["plo_t5"] + .25 * (te["phi_t5"] - te["plo_t5"])
        out.append(te)
    T = pd.concat(out); T["ev"] = T["p3"] * T["ex"]; return T
def pline(nm, T):
    ll = float(np.mean(np.where(T["top3"] == 1, np.log(T["p3"]), np.log(1 - T["p3"]))))
    s = ""
    for ev in (1.0, 1.1, 1.2, 1.3):
        b = T[T["ev"] >= ev]; s += f"  ≥{ev} {len(b):5d}点 {b['pay_r'].mean():6.1%}"
    print(f"{nm:12s} LL {ll:.5f}{s}", flush=True)
for nm, f in sets.items():
    pline(nm, place(f))
print("--- 複勝 オッズ帯別 (+AI、EV≥1.1)", flush=True)
T = place(sets["+AI"]); b = T[T["ev"] >= 1.1]
for lo, hi in ((1, 1.5), (1.5, 2.5), (2.5, 4), (4, 7), (7, 15), (15, 999)):
    g = b[(b["ex"] >= lo) & (b["ex"] < hi)]
    print(f"  {lo}-{hi}倍 {len(g):5d}点 的中{g['top3'].mean():.1%} 回収 {g['pay_r'].mean():.1%}")
print(f"{time.time() - T0:.0f}s")
