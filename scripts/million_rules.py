"""3連単100万円超えが出たレースから「荒れる法則」を探す。

1. 中央のレースを、出走前に分かる条件 (web/million.py の CONDITIONS) で表にする
2. 選定期間で、条件1〜3個の組み合わせごとに 100万馬券の出た割合を数える
3. 割合の下限 (Wilson 95%) が高く、互いに重なりすぎない組み合わせを法則として選ぶ
4. 確認期間 (選定に使っていないレース) で、本当に出やすいかを確かめる
5. 確認でも全体平均の1.5倍以上だった法則を site_meta['million_rules'] に保存

使い方: python scripts/million_rules.py [--save]
"""
import itertools
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from web import million  # noqa: E402

DB = ROOT / "data" / "keiba.db"
SPLIT = "2025-04-01"          # これより前で選び、以降で確かめる
MIN_N = 150                   # 選定期間で最低これだけのレース数がある組み合わせだけ
MAX_RULES = 8                 # オッズ無し・オッズありそれぞれ
CONFIRM_LIFT = 1.5

T0 = time.time()
conn = sqlite3.connect(DB)
h = pd.read_sql("""SELECT race_id, date, place, race_no, race_name, surface, distance, going,
                          horse_id, finish, age, sex, kinryo, odds
                   FROM results WHERE is_nar = 0""", conn)
pay = pd.read_sql("""SELECT race_id, MAX(pay) pay FROM payouts
                     WHERE bet_type = '三連単' GROUP BY race_id""", conn)
conn.close()
grade = pd.read_sql("SELECT race_id, grade FROM races", sqlite3.connect(DB)).set_index("race_id")["grade"]
h["grade"] = h["race_id"].map(grade)
h["date"] = pd.to_datetime(h["date"])
h["prior_wins"] = million.prior_wins(h)
r = million.race_table(h)
r = r.join(pay.set_index("race_id"), how="inner")
r = r[r["date"] >= "2021-01-01"]          # 2020年は前走の勝ち数が数えられないので外す
r["y"] = (r["pay"] >= million.MILLION).astype(int)
print(f"レース表 {time.time() - T0:.0f}s / {len(r)}レース / 100万馬券 {r['y'].sum()}回 "
      f"({r['y'].mean():.2%})")
r.to_pickle(ROOT / "data" / "million_races.pkl")

A = (r["date"] < SPLIT).to_numpy()
B = ~A
y = r["y"].to_numpy()
base_A, base_B = y[A].mean(), y[B].mean()
masks = million.cond_masks(r)
keys = list(masks)


def wilson_lb(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    a = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - a) / d


def stats(m):
    nA, kA = int(m[A].sum()), int(y[A & m].sum())
    nB, kB = int(m[B].sum()), int(y[B & m].sum())
    return nA, kA, nB, kB


cands = []
for size in (1, 2, 3):
    for combo in itertools.combinations(keys, size):
        # 同じ種類の条件どうし (例: 16頭以上と14頭以上) は重ねない
        groups = {k.split("_")[0].rstrip("0123456789") for k in combo}
        if len(groups) < size:
            continue
        m = np.logical_and.reduce([masks[k] for k in combo])
        nA = int(m[A].sum())
        if nA < MIN_N:
            continue
        kA = int(y[A & m].sum())
        cands.append((wilson_lb(kA, nA), combo, m))
print(f"候補 {len(cands)}通り {time.time() - T0:.0f}s")


def pick(uses_odds):
    chosen = []
    pool = [c for c in cands
            if any(million.COND[k][2] for k in c[1]) == uses_odds]
    pool.sort(key=lambda c: -c[0])
    for lb, combo, m in pool:
        if lb <= base_A * 1.3:
            break
        # 既に選んだ法則とほとんど同じレースを指すものは飛ばす
        if any((m & cm).sum() / (m | cm).sum() > 0.4 for _, _, cm in chosen):
            continue
        # 条件を1つ外しても同じくらい荒れるなら、余分な条件 (より簡単な法則を優先)
        if len(combo) > 1 and any(
                wilson_lb(y[A & sm].sum(), sm[A].sum()) >= lb * 0.97
                for sm in [np.logical_and.reduce([masks[k] for k in sub])
                           for sub in itertools.combinations(combo, len(combo) - 1)]):
            continue
        chosen.append((lb, combo, m))
        if len(chosen) >= MAX_RULES:
            break
    return chosen


rules = []
for uses_odds in (False, True):
    for lb, combo, m in pick(uses_odds):
        nA, kA, nB, kB = stats(m)
        rateA, rateB = kA / nA, (kB / nB if nB else np.nan)
        pays = r.loc[m & B, "pay"]
        rules.append({
            "keys": list(combo), "label": million.rule_label(combo), "uses_odds": uses_odds,
            "n_A": nA, "hit_A": kA, "rate_A": round(rateA, 4), "lift_A": round(rateA / base_A, 2),
            "n_B": nB, "hit_B": kB, "rate_B": round(rateB, 4),
            "lift_B": round(rateB / base_B, 2) if nB else None,
            "rate_100k_B": round(float((pays >= 100_000).mean()), 4) if len(pays) else None,
            "confirmed": bool(nB >= 20 and rateB / base_B >= CONFIRM_LIFT),
        })

pd.set_option("display.width", 220)
pd.set_option("display.max_colwidth", 60)
out = pd.DataFrame(rules)
print(f"\n全体の100万馬券の割合: 選定 {base_A:.2%} / 確認 {base_B:.2%}")
print(out[["label", "uses_odds", "n_A", "rate_A", "lift_A", "n_B", "hit_B", "rate_B",
           "lift_B", "rate_100k_B", "confirmed"]].to_string(index=False))

# 単独条件の一覧 (傾向をつかむため)
single = []
for k in keys:
    nA, kA, nB, kB = stats(masks[k])
    single.append({"条件": million.COND[k][0], "選定レース": nA,
                   "選定の割合": kA / max(nA, 1), "確認レース": nB,
                   "確認の割合": kB / max(nB, 1)})
sd = pd.DataFrame(single).sort_values("選定の割合", ascending=False)
print("\n単独の条件:\n" + sd.to_string(index=False, float_format=lambda v: f"{v:.2%}"))

# 当てはまる法則の数ごとの割合 (確認期間)
conf = [x for x in rules if x["confirmed"]]
cnt = np.zeros(len(r), dtype=int)
for x in conf:
    cnt += np.logical_and.reduce([masks[k] for k in x["keys"]])
cb = pd.DataFrame({"n": np.minimum(cnt, 3), "y": y})[B].groupby("n")["y"].agg(["size", "mean"])
print("\n確認期間: 当てはまる法則の数ごとの100万馬券の割合\n" + cb.to_string())

if "--save" in sys.argv:
    meta = {"rules": conf, "base_A": round(base_A, 4), "base_B": round(base_B, 4),
            "split": SPLIT, "n_A": int(A.sum()), "n_B": int(B.sum()),
            "hits_A": int(y[A].sum()), "hits_B": int(y[B].sum()),
            "by_count": {str(int(k)): {"n": int(v["size"]), "rate": round(float(v["mean"]), 4)}
                         for k, v in cb.iterrows()}}
    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS site_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO site_meta VALUES ('million_rules', ?)",
                 (json.dumps(meta, ensure_ascii=False),))
    conn.commit()
    conn.close()
    print(f"\n保存: 確認済みの法則 {len(conf)}件")
print(f"完了 {time.time() - T0:.0f}s")
