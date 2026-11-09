"""単勝回収率のバックテスト。

学習時の検証期間 (モデルが見ていない未来のレース) で、
モデルの勝率予測に基づいて単勝を買った場合の回収率を計算する。
オッズは確定オッズ (レース後の値) を使うため、実際よりやや楽観的になる点に注意。
"""
import argparse

import joblib
import numpy as np
import pandas as pd

from .features import add_market_features, build_training_data, load_results
from .scraper import DB_PATH
from .train import MODEL_PATH, split_by_date


def _simulate(bets, stake=100):
    """bets: DataFrame(win, odds)。単勝を stake 円ずつ買った結果を返す。"""
    n = len(bets)
    if n == 0:
        return {"bets": 0, "hits": 0, "hit_rate": None,
                "stake": 0, "payout": 0, "roi": None}
    hits = int(bets["win"].sum())
    payout = float((bets.loc[bets["win"] == 1, "odds"] * stake).sum())
    total = n * stake
    return {
        "bets": n,
        "hits": hits,
        "hit_rate": round(hits / n, 4),
        "stake": total,
        "payout": int(payout),
        "roi": round(payout / total, 4),
    }


def backtest(ev_threshold=1.2, db_path=DB_PATH, model_path=MODEL_PATH,
             max_odds=100.0):
    """検証期間で複数の単勝戦略を比較する。

    JRA の単勝払戻率は 80% (控除率20%) なので、回収率80%が「平均」の水準。
    100%超を目指すには市場を約25%上回る必要がある。

    期待値買いは市場モデルの確率を使い、オッズ上限を設ける
    (人気薄はモデルが勝率を過大評価しやすく、上限なしでは回収率が大きく落ちる)。
    """
    bundle = joblib.load(model_path)
    if "model_win" not in bundle:
        raise RuntimeError("旧形式のモデルです。「学習」を実行し直してください。")
    model_win = bundle["model_win"]

    df = load_results(db_path)
    X, meta = build_training_data(df)
    _, valid_idx, split_date = split_by_date(meta)

    vm = meta[valid_idx].copy()
    vm = vm[vm["odds"].notna()]
    X_valid = X[valid_idx].loc[vm.index]
    cals = bundle.get("calibrators", {})

    def cal(key, p):
        ir = cals.get(key)
        return np.clip(ir.predict(p), 0.0, 1.0) if ir is not None else p

    vm["p_win"] = cal("win", model_win.predict_proba(X_valid)[:, 1])
    vm["p_win_norm"] = vm.groupby("race_id")["p_win"].transform(lambda s: s / s.sum())
    vm["ev"] = vm["p_win_norm"] * vm["odds"]

    top1 = vm.sort_values("p_win_norm", ascending=False).groupby("race_id").head(1)
    pop1 = vm[vm["popularity"] == 1].groupby("race_id").head(1)

    strategies = {
        "能力モデル本命の単勝": _simulate(top1),
        "1番人気の単勝 (参考)": _simulate(pop1),
    }

    # 市場モデル (オッズを使う的中率重視モデル)
    if "model_market_win" in bundle:
        Xm = add_market_features(X_valid, vm["odds"], vm["race_id"])
        vm["mp_win"] = cal(
            "market_win", bundle["model_market_win"].predict_proba(Xm)[:, 1]
        )
        vm["mp_norm"] = vm.groupby("race_id")["mp_win"].transform(lambda s: s / s.sum())
        mtop1 = vm.sort_values("mp_win", ascending=False).groupby("race_id").head(1)
        strategies["市場モデル本命の単勝"] = _simulate(mtop1)

        # 期待値買い: 市場モデルの確率 × オッズ、かつオッズ上限あり
        vm["mev"] = vm["mp_norm"] * vm["odds"]
        ev_bets = vm[(vm["mev"] >= ev_threshold) & (vm["odds"] <= max_odds)]
        strategies[
            f"期待値買い (市場モデル EV>={ev_threshold} / オッズ≤{max_odds:g})"
        ] = _simulate(ev_bets)
        # 上限なしだと人気薄で崩れることを示す比較
        strategies[f"(比較) 同条件でオッズ上限なし"] = _simulate(
            vm[vm["mev"] >= ev_threshold]
        )
    else:
        vm["ev"] = vm["p_win_norm"] * vm["odds"]
        strategies[f"期待値買い (EV>={ev_threshold})"] = _simulate(
            vm[vm["ev"] >= ev_threshold]
        )

    return {
        "valid_from": str(pd.Timestamp(split_date).date()),
        "n_races": vm["race_id"].nunique(),
        "ev_threshold": ev_threshold,
        "max_odds": max_odds,
        "baseline": 0.80,
        "strategies": strategies,
    }


def main():
    ap = argparse.ArgumentParser(description="単勝回収率バックテスト")
    ap.add_argument("--ev", type=float, default=1.2, help="期待値の閾値")
    args = ap.parse_args()
    res = backtest(ev_threshold=args.ev)
    print(f"検証期間: {res['valid_from']} 以降 / {res['n_races']}レース")
    for name, s in res["strategies"].items():
        print(f"\n[{name}]")
        for k, v in s.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
