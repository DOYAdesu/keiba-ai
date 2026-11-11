"""回収率100%超えの余地を探す (検証期間だけ・40分前オッズで判断し、確定オッズ・実払戻で払う)。"""
import sqlite3, sys
from pathlib import Path
import joblib, numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from keiba.features import add_market_features
from keiba.oddsmove import odds_at
from keiba.scraper import DB_PATH

X, meta = pd.read_pickle(ROOT / "data" / "refine_cache.pkl")
b = joblib.load(ROOT / "models" / "model.pkl")
v = (meta["date"] >= pd.Timestamp(b["split_date"])) & (meta["is_nar"] == 0)
X, m = X[v.values].copy(), meta[v].copy().reset_index(drop=True)
X = X.reset_index(drop=True)
m["o40"] = odds_at(m).values
m["o40"] = m["o40"].fillna(m["odds"])
cal = lambda k, p: np.clip(b["calibrators"][k].predict(p), 0, 1) if k in b["calibrators"] else p
m["pa_win"] = cal("win", b["model_win"].predict_proba(X)[:, 1])           # 能力 (オッズなし)
m["pa_top3"] = cal("top3", b["model_top3"].predict_proba(X)[:, 1])
Xm = add_market_features(X, m["o40"], m["race_id"])
m["pm_win"] = cal("market_win", b["model_market_win"].predict_proba(Xm)[:, 1])
m["pm_top3"] = cal("market_top3", b["model_market_top3"].predict_proba(Xm)[:, 1])
for c in ("pa_win", "pm_win"):
    m[c] = m[c] / m.groupby("race_id")[c].transform("sum")
conn = sqlite3.connect(DB_PATH)
fk = pd.read_sql("SELECT race_id, CAST(combo AS INT) horse_no, pay FROM payouts WHERE bet_type='複勝'", conn)
m = m.merge(fk, on=["race_id", "horse_no"], how="left")
m["win"] = (m["finish"] == 1); m["top3"] = m["finish"] <= 3
m["tan_ret"] = np.where(m["win"], m["odds"] * 100, 0)
m["fuku_ret"] = m["pay"].fillna(0)
m["mkt"] = (1 / m["o40"]) / (1 / m["o40"]).groupby(m["race_id"]).transform("sum")
print(f"確認期間 {m['race_id'].nunique()}レース {len(m)}頭  全頭の単勝回収 {m['tan_ret'].mean():.1f}%  複勝 {m['fuku_ret'].mean():.1f}%")

def show(title, mask, ret="tan_ret"):
    g = m[mask]
    if len(g) < 30: return
    print(f"  {title:40s} {len(g):5d}頭 的中{(g['win'] if ret=='tan_ret' else g['top3']).mean():5.1%} 回収 {g[ret].mean():6.1f}%")

print("\n[単勝] 期待値 = AI勝率 × 40分前オッズ")
for name, p in (("市場モデル", "pm_win"), ("能力モデル(オッズなし)", "pa_win")):
    ev = m[p] * m["o40"]
    for lo, hi in ((0, .8), (.8, 1), (1, 1.2), (1.2, 1.5), (1.5, 2), (2, 99)):
        show(f"{name} EV {lo}-{hi}", (ev >= lo) & (ev < hi))
print("\n[単勝] AIと市場のずれ (能力モデル勝率 ÷ 市場の勝率)")
r = m["pa_win"] / m["mkt"]
for lo, hi in ((0, .7), (.7, 1), (1, 1.3), (1.3, 1.7), (1.7, 2.5), (2.5, 99)):
    show(f"ずれ {lo}-{hi}倍", (r >= lo) & (r < hi))
print("\n[単勝] オッズ帯 × ずれ1.3倍以上")
for lo, hi in ((1, 3), (3, 6), (6, 12), (12, 30), (30, 999)):
    show(f"オッズ{lo}-{hi} ずれ≥1.3", (m["o40"] >= lo) & (m["o40"] < hi) & (r >= 1.3))
    show(f"オッズ{lo}-{hi} 全頭", (m["o40"] >= lo) & (m["o40"] < hi))
print("\n[複勝] 能力モデル複勝率 ÷ 市場の3着内確率の目安")
mk3 = np.clip(3 * m["mkt"] * 0.9, 0, 0.95)
r3 = m["pa_top3"] / mk3
for lo, hi in ((0, 1), (1, 1.3), (1.3, 1.7), (1.7, 99)):
    show(f"複勝ずれ {lo}-{hi}倍", (r3 >= lo) & (r3 < hi), "fuku_ret")
print("\n[本命] レース内1位")
top = m.sort_values("pm_win", ascending=False).groupby("race_id").head(1)
print(f"  市場モデル本命 単勝 {top['tan_ret'].mean():.1f}% 複勝 {top['fuku_ret'].mean():.1f}%")
topa = m.sort_values("pa_win", ascending=False).groupby("race_id").head(1)
print(f"  能力モデル本命 単勝 {topa['tan_ret'].mean():.1f}% 複勝 {topa['fuku_ret'].mean():.1f}%  人気{topa['popularity'].mean():.1f}")
