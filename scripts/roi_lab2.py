"""第2弾: 買う基準・学習期間・木のモデル・オッズの滑り補正・複勝 (開発期間 2020-22 のみ)。"""
import sys, time
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from roi_lab import *  # noqa
T0 = time.time()
h = load("t5")
F = lambda *a: BASE + list(a)
COMBO = F("ai_gap", "ai3_gap", "lpm2", "pl_gap", "mv_late")
BC = F("lpm2")

print("--- A. 期待値の帯ごとの実際の回収 (校正の確認)", flush=True)
sets = {"今のモデル": wf(h, BASE, DEV), "+補正": wf(h, BC, DEV), "全部入り": wf(h, COMBO, DEV, first=2018)}
for nm, t in sets.items():
    ev = t["p"] * t["o5"]
    print(nm)
    for lo, hi in ((.6, .8), (.8, .9), (.9, 1), (1, 1.1), (1.1, 1.2), (1.2, 1.4), (1.4, 9)):
        b = t[(ev >= lo) & (ev < hi) & (t["o5"] <= 30)]
        print(f"  EV {lo}-{hi}: {len(b):6d}点 回収 {np.where(b['win'] == 1, b['odds'], 0).mean():6.1%}")
print("--- B. しきい値 × 上限オッズ", flush=True)
for nm, t in sets.items():
    print(nm)
    for ev in (1.0, 1.05, 1.1, 1.2, 1.3):
        s = ""
        for om in (8, 15, 30, 60):
            b = roi(t, ev, om)
            s += f"  ≤{om}倍 {len(b):5d}点 {b['ret'].sum() / max(len(b), 1):6.1%}"
        print(f"  EV≥{ev}:{s}", flush=True)
print("--- C. オッズ帯別 (今のモデル EV≥1)", flush=True)
t = sets["今のモデル"]
for lo, hi in ((1, 3), (3, 6), (6, 10), (10, 20), (20, 30), (30, 60), (60, 999)):
    b = roi(t, 1.0, hi, lo); b = b[b["o5"] < hi]
    print(f"  {lo}-{hi}倍 {len(b):5d}点 {b['ret'].sum() / max(len(b), 1):6.1%}")
print("--- D. 学習期間", flush=True)
def wf_win(h, feats, years, win):
    out = []
    for y in years:
        out.append(wf(h[h["year"].between(y - win, y)], feats, [y], first=y - win))
    return pd.concat(out)
for w in (2, 4):
    line(f"直近{w}年で学習", wf_win(h, BASE, DEV, w))
line("全期間で学習", sets["今のモデル"])
print("--- E. 正則化の強さ (全部入り)", flush=True)
for l2 in (1, 10, 30, 100, 300, 1000):
    line(f"L2={l2}", wf(h, COMBO, DEV, l2=l2, first=2018))
print("--- F. 木のモデルで市場とのずれを学ぶ", flush=True)
import lightgbm as lgb
fe = [c for c in h.columns if c.startswith("zx_")] + ["mv5", "mv_pre", "style", "front_bias", "back_bias", "ai_gap", "ai3_gap", "pl_gap", "mv_late", "lpm"]
def gbm(leaves, n, lr=.02):
    out = []
    for y in DEV:
        tr, te = h[(h["year"] < y) & (h["year"] >= 2018)], h[h["year"] == y].copy()
        off = lambda d: np.log(d["pm"] / (1 - d["pm"]).clip(1e-6))
        ds = lgb.Dataset(tr[fe], tr["win"], init_score=off(tr))
        m = lgb.train(dict(objective="binary", learning_rate=lr, num_leaves=leaves, min_data_in_leaf=500, feature_fraction=.5,
                           bagging_fraction=.7, bagging_freq=1, lambda_l2=50, verbose=-1, seed=1), ds, n)
        z = off(te) + m.predict(te[fe], raw_score=True)
        p = pd.Series(1 / (1 + np.exp(-z)), index=te.index)
        te["p"] = p / p.groupby(te["race_id"]).transform("sum")
        out.append(te)
    return pd.concat(out)
for lv, n in ((4, 100), (4, 300), (8, 300)):
    line(f"木 葉{lv} {n}本", gbm(lv, n))
print("--- G. オッズの滑り補正 (5分前→確定の変化を見込む)", flush=True)
def slip(t):
    # 学習年の 確定/5分前 の平均を、オッズ帯×直前の動きで引く
    out = []
    for y in DEV:
        tr, te = h[h["year"] < y], t[t["year"] == y].copy()
        k = lambda d: pd.cut(d["o5"], [0, 3, 6, 10, 20, 30, 1e4]).astype(str) + pd.cut(d["mv5"], [-9, -.2, .2, 9]).astype(str)
        r = (tr["odds"] / tr["o5"]).groupby(k(tr)).mean()
        te["o5"] = te["o5"] * k(te).map(r).fillna(1)
        out.append(te)
    return pd.concat(out)
s = slip(sets["今のモデル"])
for ev in (1.0, 1.1):
    b = roi(s, ev, 30)
    print(f"  滑り補正後 EV≥{ev}: {len(b)}点 回収 {b['ret'].sum() / len(b):.1%}")
print("--- H. 複勝 (8頭以上。5分前の複勝オッズで判断、実際の払戻で計算)", flush=True)
from sklearn.linear_model import LogisticRegression
P = h[h["fs"] >= 8].copy()
mid = (P["plo_t5"] + P["phi_t5"]) / 2
P = P[mid.notna()]; mid = mid[P.index]
ip = 1 / mid; P["ip3"] = (ip / ip.groupby(P["race_id"]).transform("sum") * 3).clip(.005, .97)
P["lip"] = np.log(P["ip3"] / (1 - P["ip3"])); P["top3"] = (P["finish"] <= 3).astype(int)
P["pay_r"] = P["pay"].fillna(0) / 100
pf = ["lip", "lpm", "lpm2", "mv5", "mv_pre", "pl_gap", "ai_gap", "ai3_gap", "style", "front_bias", "back_bias"] + ["z_" + c for c in value.EXTRA]
out = []
for y in DEV:
    tr, te = P[(P["year"] < y) & (P["year"] >= 2018)], P[P["year"] == y].copy()
    m = LogisticRegression(C=.1, max_iter=500).fit(tr[pf], tr["top3"])
    te["p3"] = m.predict_proba(te[pf])[:, 1]
    pl = tr[tr["top3"] == 1]
    kk = ((pl["pay_r"] - pl["plo_t5"]) / (pl["phi_t5"] - pl["plo_t5"]).replace(0, np.nan)).clip(-1, 2).median()
    te["ex"] = te["plo_t5"] + kk * (te["phi_t5"] - te["plo_t5"])
    out.append(te); print(f"  {y}: 実払戻の位置 k={kk:.2f}", flush=True)
T = pd.concat(out); T["ev"] = T["p3"] * T["ex"]
print(f"  全頭の複勝回収 {T['pay_r'].mean():.1%}")
for ev in (.9, 1.0, 1.05, 1.1, 1.2, 1.3):
    b = T[T["ev"] >= ev]
    print(f"  複勝 EV≥{ev}: {len(b):6d}点 的中{b['top3'].mean():.1%} 回収 {b['pay_r'].mean():6.1%}", flush=True)
print(f"{time.time() - T0:.0f}s")
