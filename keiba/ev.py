"""複勝の期待値 (EV) 計算。

複勝は事前オッズを JRA-VAN の蓄積データから直接取れないため、
「単勝オッズ・人気・頭数」から複勝配当を予測するモデルを作り、
EV = 複勝圏確率 × 予測複勝オッズ として妙味を測る。

学習は必ず学習期間のデータだけで行う (リーク防止)。
"""
import sqlite3

import numpy as np
import pandas as pd

from .scraper import DB_PATH

ODDS_FEATURES = ["odds", "popularity", "field_size", "log_odds"]
DEFAULT_EV_THRESHOLD = 1.10


def load_fukusho_payouts(db_path=DB_PATH):
    """{(race_id, 馬番): 複勝払戻金} を返す。"""
    with sqlite3.connect(db_path) as conn:
        pay = pd.read_sql(
            "SELECT race_id, combo, pay FROM payouts WHERE bet_type='複勝'", conn
        )
    if pay.empty:
        return {}
    pay["horse_no"] = pd.to_numeric(pay["combo"], errors="coerce")
    pay = pay.dropna(subset=["horse_no"])
    return {(r.race_id, int(r.horse_no)): r.pay for r in pay.itertuples()}


def _prep(frame):
    x = pd.DataFrame(index=frame.index)
    for c in ("odds", "popularity", "field_size"):
        x[c] = pd.to_numeric(frame[c], errors="coerce")
    x["log_odds"] = np.log(x["odds"].clip(lower=1.01))
    return x[ODDS_FEATURES]


def fit_fukusho_odds_model(meta, train_idx, db_path=DB_PATH):
    """複勝オッズ予測モデルを学習期間だけで学習する。データ不足なら None。"""
    import lightgbm as lgb

    paymap = load_fukusho_payouts(db_path)
    if not paymap:
        return None
    sub = meta[train_idx].copy()
    sub["fuku_pay"] = [
        paymap.get((r, int(h)) if pd.notna(h) else None)
        for r, h in zip(sub["race_id"], sub["horse_no"])
    ]
    sub = sub[sub["fuku_pay"].notna() & sub["odds"].notna() & (sub["odds"] > 0)]
    if len(sub) < 500:
        return None
    model = lgb.LGBMRegressor(
        n_estimators=400, learning_rate=0.05, num_leaves=31, verbose=-1
    )
    model.fit(_prep(sub), np.log(sub["fuku_pay"] / 100.0))
    return model


def predict_fukusho_odds(model, frame):
    """複勝オッズを予測する。model が None なら NaN。"""
    if model is None:
        return np.full(len(frame), np.nan)
    return np.exp(model.predict(_prep(frame)))


def fukusho_ev(p_top3, fuku_odds):
    """複勝の期待値。1.0 が損益分岐 (控除率込みの市場平均は約0.8)。"""
    return np.asarray(p_top3, dtype=float) * np.asarray(fuku_odds, dtype=float)
