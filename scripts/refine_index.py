"""指数 (総合) の作り方を比べる。

候補:
  現行   = AI能力モデル (オッズなし) の複勝率の順位
  内訳合成 = 精錬した10項目をロジスティック回帰で合成 (期間Aで学習)
  混合   = AI能力モデル と 内訳合成 を足し合わせる (重みは期間B前半で決め、B後半で確認)
期間B はAIモデルの学習に使っていないので、モデルの成績が水増しされない。
"""
import sqlite3
import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

ROOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

ch = pd.read_pickle(ROOT + r"\data\refine_chosen.pkl")
COMPS = [c for c in ch.columns if c not in ("race_id", "horse_no", "date", "top3")]
with sqlite3.connect(ROOT + r"\data\keiba.db") as conn:
    sp = pd.read_sql("SELECT race_id, horse_no, ability_idx FROM site_preds", conn)
ch = ch.merge(sp, on=["race_id", "horse_no"], how="left")


def pct_rank(s):
    """全体の中での位置 (0-1)。データ無しは0.5。"""
    return s.rank(pct=True).fillna(0.5)


Z = pd.DataFrame({c: pct_rank(ch[c]) for c in COMPS})
Z["ai"] = pct_rank(ch["ability_idx"])


def auc_top(score, mask):
    x = pd.DataFrame({"race": ch.loc[mask, "race_id"], "s": score[mask], "y": ch.loc[mask, "top3"]})
    x["r"] = x.groupby("race")["s"].rank(method="average")
    g = x.groupby("race")
    npos, n = g["y"].sum(), g["y"].count()
    nneg = n - npos
    spos = (x["r"] * x["y"]).groupby(x["race"]).sum()
    ok = (npos > 0) & (nneg > 0)
    auc = ((spos - npos * (npos + 1) / 2) / (npos * nneg))[ok].mean()
    top = x.sort_values("s", ascending=False).groupby("race").head(1)
    return auc, top["y"].mean()


A = (ch["date"] >= "2025-01-01") & (ch["date"] < "2026-05-19")
B = ch["date"] >= "2026-05-19"
bd = np.sort(ch.loc[B, "date"].unique())
mid = bd[len(bd) // 2]
B1 = B & (ch["date"] <= mid)
B2 = B & (ch["date"] > mid)

# 内訳合成: 期間Aで重みを学習
lr = LogisticRegression(max_iter=1000).fit(Z.loc[A, COMPS], ch.loc[A, "top3"])
comp_score = pd.Series(lr.decision_function(Z[COMPS]), index=ch.index)
print("内訳合成の重み (期間Aで学習):")
for c, w in sorted(zip(COMPS, lr.coef_[0]), key=lambda t: -abs(t[1])):
    print(f"  {c}: {w:+.2f}")

ai = Z["ai"]
cs = comp_score.rank(pct=True)
best_w, best_auc = 0.0, -1
print("\n混合の重み (期間B前半で選ぶ):")
for w in np.linspace(0, 1, 11):
    a, _ = auc_top((1 - w) * ai + w * cs, B1)
    print(f"  内訳の重み {w:.1f}: AUC {a:.4f}")
    if a > best_auc:
        best_w, best_auc = w, a

print(f"\n=== 期間B後半 (未使用) での比較 / 選んだ内訳の重み {best_w:.1f} ===")
for name, s in [("現行 (AI能力モデル)", ai), ("内訳合成のみ", cs),
                (f"混合 (内訳{best_w:.1f})", (1 - best_w) * ai + best_w * cs)]:
    a, t = auc_top(s, B2)
    print(f"  {name:22s} AUC {a:.4f}  指数1位の複勝率 {t:.1%}")

# 指数帯ごとの実績 (B全体, 混合)
mix = (1 - best_w) * ai + best_w * cs
band = pd.cut((mix[B] * 100).round(), [-1, 50, 70, 80, 90, 95, 100],
              labels=["〜50", "51-70", "71-80", "81-90", "91-95", "96-100"])
print("\n指数帯ごとの実際の複勝率 (期間B):")
print(ch.loc[B].groupby(band, observed=True)["top3"].agg(["count", "mean"]).round(3).to_string())

pd.to_pickle({"coef": dict(zip(COMPS, lr.coef_[0])), "intercept": float(lr.intercept_[0]),
              "w_comp": float(best_w)}, ROOT + r"\data\refine_index.pkl")
