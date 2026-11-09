"""1日の予算を決めて運用した場合の日別収支シミュレーション。

検証期間 (モデルが学習に使っていない期間) の各開催日について、
「その日の予算を全レースに等分して買う」戦略の収支を実際の配当で計算する。
"""
import argparse
import sqlite3

import joblib
import numpy as np
import pandas as pd

from .features import add_market_features, build_training_data, load_results
from .scraper import DB_PATH
from .train import MODEL_PATH, split_by_date

UNIT = 100  # 馬券の最小単位

# 戦略: (名前, 説明, 1レースあたりの点数, 買い目を作る関数)
STRATEGIES = {
    "◎複勝": ("本命の複勝を1点", 1, lambda o: [("複勝", _c(o[0]))]),
    "◎単勝": ("本命の単勝を1点", 1, lambda o: [("単勝", _c(o[0]))]),
    "◎-○馬連": ("上位2頭の馬連を1点", 1,
              lambda o: [("馬連", _c(o[0], o[1]))] if len(o) >= 2 else []),
    "◎-○ワイド": ("上位2頭のワイドを1点", 1,
               lambda o: [("ワイド", _c(o[0], o[1]))] if len(o) >= 2 else []),
    "◎複勝+◎-○馬連": ("複勝と馬連を各1点", 2,
                 lambda o: [("複勝", _c(o[0]))] +
                           ([("馬連", _c(o[0], o[1]))] if len(o) >= 2 else [])),
    "三連複◎軸流し": ("本命軸・相手4頭で6点", 6, lambda o: [
        ("三連複", _c(o[0], a, b))
        for i, a in enumerate(o[1:5]) for b in o[i + 2:5]
    ]),
    "EV選別複勝 (妙味のある馬だけ)": ("期待値が閾値以上の馬の複勝のみ", 1, None),
}


def _c(*nos):
    return "".join(f"{int(n):02d}" for n in sorted(nos))


def _confidence(probs):
    """本命の複勝圏確率と2番手との差から確信度を出す (daily.py と同じ式)。"""
    top = float(probs[0])
    gap = float(probs[0] - probs[1]) if len(probs) > 1 else 0.0
    return top * 0.7 + gap * 1.5


EV_STRATEGY = "EV選別複勝 (妙味のある馬だけ)"


def simulate_ev_days(budget=10000, ev_threshold=1.10,
                     db_path=DB_PATH, model_path=MODEL_PATH):
    """期待値が閾値以上の馬の複勝だけを買う戦略を日別に検証する。

    1レース1頭に限らず、その日の全出走馬から妙味のある馬を選ぶ。
    """
    from .ev import fukusho_ev, load_fukusho_payouts, predict_fukusho_odds

    bundle = joblib.load(model_path)
    fom = bundle.get("fuku_odds_model")
    if fom is None:
        raise RuntimeError(
            "複勝オッズ予測モデルがありません。「学習」を実行し直してください。"
        )

    df = load_results(db_path)
    X, meta = build_training_data(df)
    _, valid_idx, split_date = split_by_date(meta)
    paymap = load_fukusho_payouts(db_path)
    races_with_pay = {rid for rid, _ in paymap}

    vm = meta[valid_idx].copy()
    keep = (vm["odds"].notna() & (vm["odds"] > 0)
            & vm["race_id"].isin(races_with_pay))
    vm = vm[keep]
    Xv = X[valid_idx].loc[vm.index]
    if vm.empty:
        return {"strategy": EV_STRATEGY, "daily": pd.DataFrame(), "summary": None}

    Xm = add_market_features(Xv, vm["odds"], vm["race_id"])
    cals = bundle.get("calibrators", {})

    def cal(key, p):
        ir = cals.get(key)
        return np.clip(ir.predict(p), 0.0, 1.0) if ir is not None else p

    vm["p3"] = cal("market_top3",
                   bundle["model_market_top3"].predict_proba(Xm)[:, 1])
    vm["pred_fuku_odds"] = predict_fukusho_odds(fom, vm)
    vm["ev"] = fukusho_ev(vm["p3"], vm["pred_fuku_odds"])
    vm["ret"] = [
        paymap.get((r, int(h)), 0) if pd.notna(h) else 0
        for r, h in zip(vm["race_id"], vm["horse_no"])
    ]

    sel = vm[vm["ev"] >= ev_threshold].copy()
    rows = []
    for day, g in sel.groupby(sel["date"].dt.date):
        g = g.sort_values("ev", ascending=False)
        max_points = max(1, budget // UNIT)
        g = g.head(max_points)
        per_point = max(UNIT, int(budget / len(g) // UNIT) * UNIT)
        stake = per_point * len(g)
        payout = int((g["ret"] * per_point / 100).sum())
        rows.append({
            "日付": str(day), "レース数": g["race_id"].nunique(), "点数": len(g),
            "1点あたり": per_point, "投資額": stake, "払戻額": payout,
            "収支": payout - stake, "的中": int((g["ret"] > 0).sum()),
            "回収率": payout / stake if stake else None,
        })

    daily = pd.DataFrame(rows)
    if daily.empty:
        return {"strategy": EV_STRATEGY, "daily": daily, "summary": None}
    prof = daily["収支"]
    summary = {
        "日数": len(daily), "平均収支": int(prof.mean()),
        "中央値収支": int(prof.median()), "最高": int(prof.max()),
        "最低": int(prof.min()), "プラスの日": int((prof > 0).sum()),
        "プラス率": float((prof > 0).mean()),
        "合計投資": int(daily["投資額"].sum()),
        "合計払戻": int(daily["払戻額"].sum()),
        "通算収支": int(prof.sum()),
        "通算回収率": float(daily["払戻額"].sum() / daily["投資額"].sum()),
        "予算超過の日": int((daily["投資額"] > budget).sum()),
    }
    return {"strategy": EV_STRATEGY, "daily": daily, "summary": summary}


def simulate_days(budget=10000, strategy="◎複勝", min_confidence=0.0,
                  db_path=DB_PATH, model_path=MODEL_PATH, ev_threshold=1.10):
    if strategy == EV_STRATEGY:
        return simulate_ev_days(budget, ev_threshold, db_path, model_path)
    return _simulate_fixed(budget, strategy, min_confidence, db_path, model_path)


def _simulate_fixed(budget=10000, strategy="◎複勝", min_confidence=0.0,
                    db_path=DB_PATH, model_path=MODEL_PATH):
    """日別収支をシミュレーションする。

    budget: 1日の予算(円)。その日に買うレース数で等分する。
    min_confidence: この値以上の確信度のレースだけ買う (0 なら全レース)。
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"未知の戦略: {strategy}")
    _, _, n_points, make_bets = (strategy, *STRATEGIES[strategy])

    bundle = joblib.load(model_path)
    if "model_market_top3" not in bundle:
        raise RuntimeError("市場モデルがありません。「学習」を実行し直してください。")

    df = load_results(db_path)
    X, meta = build_training_data(df)
    _, valid_idx, split_date = split_by_date(meta)
    vm = meta[valid_idx].copy()
    Xv = X[valid_idx]
    keep = vm["odds"].notna() & (vm["odds"] > 0)
    vm, Xv = vm[keep], Xv[keep.values]

    Xm = add_market_features(Xv, vm["odds"], vm["race_id"])
    cals = bundle.get("calibrators", {})

    def cal(key, p):
        ir = cals.get(key)
        return np.clip(ir.predict(p), 0.0, 1.0) if ir is not None else p

    vm["score"] = cal("market_top3",
                      bundle["model_market_top3"].predict_proba(Xm)[:, 1])

    with sqlite3.connect(db_path) as conn:
        pay = pd.read_sql("SELECT race_id, bet_type, combo, pay FROM payouts", conn)
    pay = pay[pay["race_id"].isin(set(vm["race_id"]))]
    table = {(r.race_id, r.bet_type, r.combo): r.pay for r in pay.itertuples()}
    have_pay = set(pay["race_id"])

    # レースごとに買い目と確信度を用意
    per_race = {}
    for rid, g in vm[vm["race_id"].isin(have_pay)].groupby("race_id"):
        g = g.sort_values("score", ascending=False)
        order = g["horse_no"].tolist()
        bets = make_bets(order)
        if not bets:
            continue
        per_race[rid] = {
            "date": g["date"].iloc[0],
            "conf": _confidence(g["score"].tolist()),
            "bets": bets,
        }

    # 日別に集計
    days = {}
    for rid, r in per_race.items():
        if r["conf"] < min_confidence:
            continue
        days.setdefault(str(pd.Timestamp(r["date"]).date()), []).append((rid, r))

    rows = []
    for day, items in sorted(days.items()):
        # 予算内に収まる範囲で、確信度の高いレースから買う
        max_races = max(1, budget // (UNIT * n_points))
        items = sorted(items, key=lambda t: -t[1]["conf"])[:max_races]
        n_races = len(items)
        per_point = int(budget / (n_races * n_points) // UNIT) * UNIT
        per_point = max(per_point, UNIT)
        stake = payout = hits = points = 0
        for rid, r in items:
            for bet_type, combo in r["bets"]:
                stake += per_point
                points += 1
                p = table.get((rid, bet_type, combo))
                if p:
                    hits += 1
                    payout += int(p * per_point / 100)
        rows.append({
            "日付": day, "レース数": n_races, "点数": points,
            "1点あたり": per_point, "投資額": stake, "払戻額": payout,
            "収支": payout - stake, "的中": hits,
            "回収率": payout / stake if stake else None,
        })

    daily = pd.DataFrame(rows)
    if daily.empty:
        return {"strategy": strategy, "daily": daily, "summary": None}

    prof = daily["収支"]
    summary = {
        "日数": len(daily),
        "平均収支": int(prof.mean()),
        "中央値収支": int(prof.median()),
        "最高": int(prof.max()),
        "最低": int(prof.min()),
        "プラスの日": int((prof > 0).sum()),
        "プラス率": float((prof > 0).mean()),
        "合計投資": int(daily["投資額"].sum()),
        "合計払戻": int(daily["払戻額"].sum()),
        "通算収支": int(prof.sum()),
        "通算回収率": float(daily["払戻額"].sum() / daily["投資額"].sum()),
        "予算超過の日": int((daily["投資額"] > budget).sum()),
    }
    return {"strategy": strategy, "daily": daily, "summary": summary}


def main():
    ap = argparse.ArgumentParser(description="1日の予算での収支シミュレーション")
    ap.add_argument("--budget", type=int, default=10000)
    ap.add_argument("--strategy", default="◎複勝", choices=list(STRATEGIES))
    ap.add_argument("--min-confidence", type=float, default=0.0)
    args = ap.parse_args()

    res = simulate_days(args.budget, args.strategy, args.min_confidence)
    if res["summary"] is None:
        print("対象レースがありませんでした。")
        return
    print(f"戦略: {res['strategy']} / 1日の予算 {args.budget:,}円 "
          f"/ 確信度{args.min_confidence}以上")
    s = res["summary"]
    for k, v in s.items():
        if k in ("プラス率", "通算回収率"):
            print(f"  {k}: {v:.1%}")
        elif isinstance(v, int) and k not in ("日数", "プラスの日", "予算超過の日"):
            print(f"  {k}: {v:+,}円")
        else:
            print(f"  {k}: {v}")
    print()
    d = res["daily"].copy()
    d["回収率"] = d["回収率"].map(lambda v: f"{v:.0%}" if pd.notna(v) else "-")
    print(d.tail(12).to_string(index=False))


if __name__ == "__main__":
    main()
