"""回収率の種を探す: 当日の馬場バイアス / 発走直前の資金の流れ。2020年〜全レース、年別にも見る。"""
import sqlite3, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from keiba.oddsmove import odds_at
from keiba.scraper import DB_PATH
conn = sqlite3.connect(DB_PATH)
r = pd.read_sql("SELECT race_id, date, place, race_no, surface, horse_no, waku, finish, odds, corner1, horse_id "
                "FROM results WHERE is_nar=0 AND date>='2020-01-01' AND surface IN ('芝','ダ')", conn)
r["fs"] = r.groupby("race_id")["finish"].transform("size")
r["c1r"] = (r["corner1"] - 1) / (r["fs"] - 1).clip(lower=1)
r = r.sort_values(["horse_id", "date"])
r["style"] = r.groupby("horse_id")["c1r"].transform(lambda s: s.shift().rolling(5, min_periods=2).mean())
r["win"] = r["finish"] == 1
r["ret"] = np.where(r["win"], r["odds"] * 100, 0)
fk = pd.read_sql("SELECT race_id, CAST(combo AS INT) horse_no, pay FROM payouts WHERE bet_type='複勝'", conn)
r = r.merge(fk, on=["race_id", "horse_no"], how="left"); r["fret"] = r["pay"].fillna(0)
r["year"] = r["date"].str[:4]

# 当日の馬場バイアス: 同じ日・同じ場・同じ芝ダの「それまでのレース」の3着内馬の1角位置の平均
top = r[r["finish"] <= 3].groupby(["date", "place", "surface", "race_no"])["c1r"].mean().rename("t3c1").reset_index()
top = top.sort_values("race_no")
top["bias"] = top.groupby(["date", "place", "surface"])["t3c1"].transform(lambda s: s.shift().expanding().mean())
top["n_prev"] = top.groupby(["date", "place", "surface"]).cumcount()
r = r.merge(top[["date", "place", "surface", "race_no", "bias", "n_prev"]], on=["date", "place", "surface", "race_no"], how="left")
def rep(title, g):
    if len(g) < 200: return
    by = g.groupby("year")["ret"].mean().round(0).astype(int).to_dict()
    print(f"  {title:44s} {len(g):6d}頭 単勝回収 {g['ret'].mean():5.1f}% 複勝 {g['fret'].mean():5.1f}%  年別単勝 {by}")
print("全頭", f"{r['ret'].mean():.1f}%")
b = r[r["n_prev"] >= 3]
print("[当日の馬場バイアス] それまで3レース以上ある日")
rep("前残りの日 (bias<=0.30) × 先行馬 (style<=0.3)", b[(b["bias"] <= 0.30) & (b["style"] <= 0.3)])
rep("前残りの日 × 先行馬 × 単勝20倍以下", b[(b["bias"] <= 0.30) & (b["style"] <= 0.3) & (b["odds"] <= 20)])
rep("差しの日 (bias>=0.5) × 差し馬 (style>=0.55)", b[(b["bias"] >= 0.5) & (b["style"] >= 0.55)])
rep("差しの日 × 差し馬 × 単勝20倍以下", b[(b["bias"] >= 0.5) & (b["style"] >= 0.55) & (b["odds"] <= 20)])
rep("比較: 先行馬すべて", b[b["style"] <= 0.3])
rep("比較: 差し馬すべて", b[b["style"] >= 0.55])

# 発走直前の資金の流れ: 40分前→5分前でオッズがどれだけ動いたか (5分前に買い、確定オッズで払う)
r["o40"] = odds_at(r, "t40").values; r["o5"] = odds_at(r, "t5").values
mv = r["o5"] / r["o40"]
print("[直前の資金] 40分前→5分前のオッズの比")
for lo, hi, t in ((0, .7, "大きく売れた"), (.7, .85, "売れた"), (.85, 1.15, "変わらず"), (1.15, 1.4, "人気落ち"), (1.4, 99, "大きく人気落ち")):
    rep(f"{t} ({lo}-{hi})", r[(mv > lo) & (mv <= hi)])
    rep(f"{t} かつ 5分前10倍以下", r[(mv > lo) & (mv <= hi) & (r["o5"] <= 10)])
