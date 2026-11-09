"""前走の回顧から作る特徴量 (上がり順位・着差・流れの有利不利・位置取りの乱れ)。

web/review.py の回顧と同じ考え方を、学習用の表 (1行=1頭) にそのまま足せる形にしたもの。
どれも「そのレースより前」の値だけを使う。
"""
import numpy as np
import pandas as pd

REVIEW_FEATURES = [
    "prev_agari_rank_r", "prev_to_win", "prev_flow_bad", "prev_flow_good",
    "prev_pos_late", "avg_agari_rank_r5", "avg_to_win5",
]


def race_review_cols(df):
    """各出走 (そのレース自体) の回顧の値。df: race_id, horse_id, date, finish, corner1, agari, time_sec"""
    d = df
    ok = d["finish"].notna() & (d["finish"] > 0)
    n = d.groupby("race_id")["horse_id"].transform("size")
    c1r = (d["corner1"] - 1) / (n - 1).clip(lower=1)
    ag = d["agari"].where(ok & (d["agari"] > 0))
    ag_rank = ag.groupby(d["race_id"]).rank(method="min")
    ag_r = (ag_rank - 1) / (n - 1).clip(lower=1)
    win_t = d["time_sec"].where(ok & (d["finish"] == 1)).groupby(d["race_id"]).transform("min")
    to_win = (d["time_sec"] - win_t).clip(lower=0, upper=5)
    top3_c1r = c1r.where(ok & (d["finish"] <= 3)).groupby(d["race_id"]).transform("mean")
    front, back = c1r <= 0.3, c1r >= 0.6
    flow_bad = ((top3_c1r <= 0.25) & back) | ((top3_c1r >= 0.5) & front)
    flow_good = ((top3_c1r <= 0.25) & front) | ((top3_c1r >= 0.5) & back)
    na = c1r.isna() | top3_c1r.isna()
    return pd.DataFrame({
        "c1r": c1r, "ag_r": ag_r, "to_win": to_win,
        "flow_bad": flow_bad.astype(float).mask(na),
        "flow_good": flow_good.astype(float).mask(na),
    }, index=d.index)


def add_review_features(meta):
    """meta (build_training_data の2つ目の戻り値・date順) に対する回顧の特徴量 (DataFrame)。"""
    rc = race_review_cols(meta)
    rc["horse_id"] = meta["horse_id"].values
    g = rc.groupby("horse_id", sort=False)
    usual = g["c1r"].transform(lambda s: s.shift().rolling(5, min_periods=2).mean())
    out = pd.DataFrame(index=meta.index)
    out["prev_agari_rank_r"] = g["ag_r"].shift()
    out["prev_to_win"] = g["to_win"].shift()
    out["prev_flow_bad"] = g["flow_bad"].shift()
    out["prev_flow_good"] = g["flow_good"].shift()
    out["prev_pos_late"] = (rc["c1r"] - usual).groupby(rc["horse_id"]).shift()
    out["avg_agari_rank_r5"] = g["ag_r"].transform(
        lambda s: s.shift().rolling(5, min_periods=1).mean())
    out["avg_to_win5"] = g["to_win"].transform(
        lambda s: s.shift().rolling(5, min_periods=1).mean())
    return out[REVIEW_FEATURES].astype(float)
