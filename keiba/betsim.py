"""実際の払戻データを使った券種別バックテスト。

predict.suggest_bets と同じロジックで検証期間の各レースの買い目を作り、
JRA-VAN の払戻 (payouts テーブル) で実際の回収率を計算する。
"""
import argparse
import itertools
import sqlite3

import joblib
import numpy as np
import pandas as pd

from .bets import normalize_win_probs
from .features import add_market_features, build_training_data, load_results
from .scraper import DB_PATH
from .train import MODEL_PATH, split_by_date

UNIT = 100  # 1点あたりの購入額(円)


def load_payouts(race_ids, db_path=DB_PATH):
    """{race_id: {(券種, 組番): 払戻金}} を返す。"""
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql("SELECT race_id, bet_type, combo, pay FROM payouts", conn)
    df = df[df["race_id"].isin(set(race_ids))]
    out = {}
    for rid, g in df.groupby("race_id"):
        out[rid] = {(r["bet_type"], r["combo"]): r["pay"] for _, r in g.iterrows()}
    return out


def _combo(*nos):
    """組番文字列 (馬番を2桁ゼロ埋めして昇順連結)。"""
    return "".join(f"{int(n):02d}" for n in sorted(nos))


def build_bets(order):
    """予想順位順の馬番リストから買い目を作る。

    返り値: [(券種, 組番, 点数)]。suggest_bets と同じ構成。
    """
    bets = []
    if not order:
        return bets
    a = order[0]
    bets.append(("単勝", f"{int(a):02d}", 1))
    bets.append(("複勝", f"{int(a):02d}", 1))
    if len(order) >= 2:
        bets.append(("馬連", _combo(a, order[1]), 1))
        bets.append(("ワイド", _combo(a, order[1]), 1))
    if len(order) >= 3:
        bets.append(("ワイド", _combo(a, order[2]), 1))
    partners = order[1:5]
    if len(partners) >= 2:
        for i, j in itertools.combinations(partners, 2):
            bets.append(("三連複", _combo(a, i, j), 1))
    return bets


def simulate(db_path=DB_PATH, model_path=MODEL_PATH, use_market=True):
    """検証期間で買い目を全部買った場合の券種別回収率を計算する。"""
    bundle = joblib.load(model_path)
    df = load_results(db_path)
    X, meta = build_training_data(df)
    _, valid_idx, split_date = split_by_date(meta)

    vm = meta[valid_idx].copy()
    Xv = X[valid_idx]
    if use_market and "model_market_win" in bundle:
        Xm = add_market_features(Xv, vm["odds"], vm["race_id"])
        vm["score3"] = bundle["model_market_top3"].predict_proba(Xm)[:, 1]
        model_name = "市場モデル"
    else:
        vm["score3"] = bundle["model_top3"].predict_proba(Xv)[:, 1]
        model_name = "能力モデル"

    pays = load_payouts(vm["race_id"].unique(), db_path)

    stats = {}
    n_races = 0
    for rid, g in vm.groupby("race_id"):
        table = pays.get(rid)
        if not table:
            continue
        n_races += 1
        order = g.sort_values("score3", ascending=False)["horse_no"].tolist()
        for bet_type, combo, pts in build_bets(order):
            s = stats.setdefault(bet_type, {"bets": 0, "hits": 0, "stake": 0, "payout": 0})
            s["bets"] += pts
            s["stake"] += pts * UNIT
            pay = table.get((bet_type, combo))
            if pay:
                s["hits"] += 1
                s["payout"] += pay  # 払戻は100円あたりの金額

    rows = []
    total = {"bets": 0, "hits": 0, "stake": 0, "payout": 0}
    for bet_type, s in stats.items():
        for k in total:
            total[k] += s[k]
        rows.append({
            "券種": bet_type,
            "購入点数": s["bets"],
            "的中数": s["hits"],
            "的中率": s["hits"] / s["bets"] if s["bets"] else None,
            "投資額": s["stake"],
            "払戻額": s["payout"],
            "回収率": s["payout"] / s["stake"] if s["stake"] else None,
        })
    rows.append({
        "券種": "合計", "購入点数": total["bets"], "的中数": total["hits"],
        "的中率": total["hits"] / total["bets"] if total["bets"] else None,
        "投資額": total["stake"], "払戻額": total["payout"],
        "回収率": total["payout"] / total["stake"] if total["stake"] else None,
    })
    return {
        "model": model_name,
        "valid_from": str(pd.Timestamp(split_date).date()),
        "n_races": n_races,
        "table": pd.DataFrame(rows),
    }


def main():
    ap = argparse.ArgumentParser(description="券種別回収率シミュレーション")
    ap.add_argument("--ability", action="store_true", help="能力モデルで検証")
    args = ap.parse_args()
    res = simulate(use_market=not args.ability)
    print(f"{res['model']} / 検証期間 {res['valid_from']} 以降 / {res['n_races']:,}レース")
    t = res["table"].copy()
    t["的中率"] = t["的中率"].map(lambda v: f"{v:.1%}" if pd.notna(v) else "-")
    t["回収率"] = t["回収率"].map(lambda v: f"{v:.1%}" if pd.notna(v) else "-")
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
