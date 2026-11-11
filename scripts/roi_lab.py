"""回収率の実験 (data/roi_lab.pkl を使う)。

ルール:
  - どの年も「その年より前だけで学習 → その年を買う」。
  - 案の選定は 2020〜2022 (開発期間) だけで行う。2023〜 (確認期間) は最後に1回だけ見る。
  - 回収率はぶれが大きいので、まず尤度 (当たり方の良さ) で選び、回収率は確認に使う。
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from keiba import value  # noqa: E402

BASE = list(value.FEATS)
DEV = (2020, 2021, 2022)
HOLD = (2023, 2024, 2025, 2026)


def load(tag="t5"):
    h = pd.read_pickle(ROOT / "data" / "roi_lab.pkl")
    h["o5"] = h[f"w_{tag}"]
    h = h[h["o5"].notna() & (h["o5"] > 0)]
    h = h[h.groupby("race_id")["o5"].transform("size") == h["fs"]].copy()
    inv = 1 / h["o5"]
    h["pm"] = inv / inv.groupby(h["race_id"]).transform("sum")
    h["lpm"] = np.log(h["pm"])
    h["mv5"] = np.log(h["o5"] / h["o40"]).clip(-1.5, 1.5).fillna(0)
    h["drift_good"] = ((h["o5"] / h["o40"] >= 1.4) & (h["o5"] <= 10)).astype(float)
    h["steam"] = (h["o5"] / h["o40"] <= 0.7).astype(float)
    g = h["race_id"]
    # 追加の材料
    an = h["ai_win"] / h["ai_win"].groupby(g).transform("sum")
    h["ai_gap"] = np.log(an / h["pm"]).clip(-2, 2).fillna(0)
    h["ai3_gap"] = (h["ai_top3"] - h["ai_top3"].groupby(g).transform("mean")).fillna(0) * 5
    h["lpm2"] = h["lpm"] ** 2
    h["long"] = (h["o5"] >= 30).astype(float)
    pmid = (h[f"plo_{tag}"] + h[f"phi_{tag}"]) / 2
    ip = 1 / pmid
    ip3 = ip / ip.groupby(g).transform("sum") * 3
    h["pl_gap"] = np.log(ip3 / (3 * h["pm"]).clip(upper=0.95)).clip(-2, 2).fillna(0)
    h["pl_gap"] -= h["pl_gap"].groupby(g).transform("mean")
    if tag == "t5":
        h["mv_late"] = np.log(h["o5"] / h["w_t10"]).clip(-1, 1).fillna(0)
    else:
        h["mv_late"] = np.log(h["o5"] / h["w_t20"]).clip(-1, 1).fillna(0)
    for c in [c for c in h.columns if c.startswith("x_")]:
        v = pd.to_numeric(h[c], errors="coerce")
        gg = v.groupby(g)
        h["z" + c] = ((v - gg.transform("mean")) / gg.transform("std").replace(0, np.nan)).fillna(0).clip(-3, 3)
    return h.reset_index(drop=True)


def wf(h, feats, years, l2=value.L2, first=2016, mask=None):
    """前向きに予測した p を持つテスト行を返す。"""
    value.FEATS = feats
    out = []
    for y in years:
        tr = h[(h["year"] < y) & (h["year"] >= first)]
        te = h[h["year"] == y]
        if mask is not None:
            tr, te = tr[mask(tr)], te[mask(te)]
        c = value.fit(tr, l2)
        te = te.copy()
        te["p"] = value.predict(te, c)
        out.append(te)
    return pd.concat(out)


def race_ll(t, col="p"):
    w = t[t["win"] == 1]
    return float(np.log(w[col].clip(1e-6)).mean())


def boot(b, n=2000, seed=0):
    """開催日ごとに引き直した回収率の95%区間。"""
    d = b.groupby("date").agg(r=("ret", "sum"), s=("stake", "sum"))
    rng = np.random.default_rng(seed)
    i = rng.integers(0, len(d), (n, len(d)))
    v = d["r"].to_numpy()[i].sum(1) / d["s"].to_numpy()[i].sum(1)
    return np.percentile(v, [2.5, 97.5])


def roi(t, ev=1.0, omax=30.0, omin=0.0):
    b = t[(t["p"] * t["o5"] >= ev) & (t["o5"] <= omax) & (t["o5"] >= omin)].copy()
    b["stake"] = 1.0
    b["ret"] = np.where(b["win"] == 1, b["odds"], 0.0)
    return b


def line(name, t, ev=1.0, omax=30.0):
    b = roi(t, ev, omax)
    r = b["ret"].sum() / max(len(b), 1)
    lo, hi = boot(b) if len(b) > 30 else (np.nan, np.nan)
    print(f"{name:34s} LL {race_ll(t):.5f} 点数 {len(b):5d} 回収 {r:6.1%} [{lo:5.1%},{hi:5.1%}]", flush=True)


if __name__ == "__main__":
    T0 = time.time()
    h = load("t5")
    print("--- 今のモデルの年別 (2018〜2021 は案の選定に一度も使っていない期間)", flush=True)
    t = wf(h, BASE, range(2018, 2027))
    for y, g in t.groupby("year"):
        b = roi(g)
        lo, hi = boot(b)
        print(f"{y}: {len(b):4d}点 回収 {b['ret'].sum() / len(b):6.1%} [{lo:5.1%},{hi:5.1%}]  市場LL {race_ll(g, 'pm'):.4f} モデルLL {race_ll(g):.4f}", flush=True)
    c = t[t["year"].between(2018, 2021)]
    line("未使用期間 2018-2021 合計", c)
    print("\n--- 判断の時点 (今の判定は発走8分前。t5 は5分前)", flush=True)
    h10 = load("t10")
    t10 = wf(h10, BASE, range(2018, 2027))
    for nm, a, b_ in (("5分前", t, None), ("10分前", t10, None)):
        for lab, ys in (("2018-21", range(2018, 2022)), ("2022-26", range(2022, 2027))):
            line(f"{nm} {lab}", a[a["year"].isin(ys)])
    print("\n--- 材料の案 (開発期間 2020-22 のみ)", flush=True)
    F = lambda *a: BASE + list(a)
    zall = [c for c in h.columns if c.startswith("zx_")]
    variants = {
        "今のモデル": BASE, "市場のみ": ["lpm"],
        "+AI勝率のずれ": F("ai_gap"), "+AI複勝のずれ": F("ai3_gap"), "+AI両方": F("ai_gap", "ai3_gap"),
        "+本命大穴の補正": F("lpm2"), "+複勝票のずれ": F("pl_gap"), "+最後の動き": F("mv_late"),
        "+全特徴量": BASE + zall,
        "+AI+複勝+補正+動き": F("ai_gap", "ai3_gap", "lpm2", "pl_gap", "mv_late"),
    }
    for nm, fe in variants.items():
        line(nm, wf(h, fe, DEV, first=2018))
    print(f"\n{time.time() - T0:.0f}s")
