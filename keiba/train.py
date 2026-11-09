"""LightGBM でモデルを学習する。

- top3 モデル: 3着以内(複勝圏)に入る確率
- win モデル: 1着になる確率 (買い目・回収率シミュレーションに使用)
"""
import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from .features import (
    FEATURES, MARKET_FEATURES, add_market_features, build_training_data, load_results,
)
from .scraper import DB_PATH, PROJECT_ROOT

MODEL_DIR = PROJECT_ROOT / "models"
MODEL_PATH = MODEL_DIR / "model.pkl"

MIN_ROWS = 500


def _fit_lgbm(X_train, y_train, X_valid, y_valid):
    import lightgbm as lgb

    model = lgb.LGBMClassifier(
        objective="binary",
        n_estimators=2000,
        learning_rate=0.05,
        num_leaves=63,
        min_child_samples=30,
        subsample=0.9,
        subsample_freq=1,
        colsample_bytree=0.8,
        random_state=42,
        verbose=-1,
    )
    model.fit(
        X_train, y_train,
        eval_X=X_valid, eval_y=y_valid,
        eval_metric="auc",
        callbacks=[lgb.early_stopping(100, verbose=False)],
    )
    return model


def fit_calibrator(y_true, prob):
    """Isotonic 回帰で確率を校正する (競馬辞典 第12章 §11)。

    「勝率30%」と予測した馬が実際に約30%勝つように補正する。
    """
    from sklearn.isotonic import IsotonicRegression

    ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    ir.fit(prob, y_true)
    return ir


def calibration_error(y_true, prob, bins=10):
    """ECE (Expected Calibration Error): 予測確率と実際の的中率のズレ。"""
    y_true = np.asarray(y_true, dtype=float)
    prob = np.asarray(prob, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(prob, edges[1:-1]), 0, bins - 1)
    err = 0.0
    for b in range(bins):
        m = idx == b
        if not m.any():
            continue
        err += m.mean() * abs(prob[m].mean() - y_true[m].mean())
    return float(err)


VALID_DAYS = 180   # 検証に取っておく直近の日数 (残りは全部学習に使う)


def split_by_date(meta):
    """時系列で分割し (train_idx, valid_idx, split_date) を返す。

    データが数年分あるときは直近 VALID_DAYS 日を検証に、それより前を全部学習に使う
    (2020年からのデータで学習すると、2025年からだけより当たり方が良かった:
    scripts/refine_data.py)。データが短いときは約 80/20。
    """
    dates = np.sort(meta["date"].unique())
    if len(dates) < 2:
        raise RuntimeError("開催日が1日分しかありません。データ期間を広げてください。")
    split_date = dates[min(int(len(dates) * 0.8), len(dates) - 2)]
    recent = pd.Timestamp(dates[-1]) - pd.Timedelta(days=VALID_DAYS)
    if recent > pd.Timestamp(split_date):
        split_date = dates[np.searchsorted(dates, np.datetime64(recent))]
    train_idx = meta["date"] <= split_date
    return train_idx, ~train_idx, pd.Timestamp(split_date)


def train(db_path=DB_PATH, model_path=MODEL_PATH):
    """top3 / win の2モデルを学習し、メトリクスの dict を返す。"""
    df = load_results(db_path)
    if len(df) < MIN_ROWS:
        raise RuntimeError(
            f"学習データが不足しています ({len(df)}行)。先にデータ収集を実行してください。"
        )

    X, meta = build_training_data(df)
    train_idx, valid_idx, split_date = split_by_date(meta)

    model_top3 = _fit_lgbm(
        X[train_idx], meta["top3"][train_idx], X[valid_idx], meta["top3"][valid_idx]
    )
    model_win = _fit_lgbm(
        X[train_idx], meta["win"][train_idx], X[valid_idx], meta["win"][valid_idx]
    )

    # 市場モデル: オッズを特徴量に含める (的中率重視)
    Xm = add_market_features(X, meta["odds"], meta["race_id"])
    model_m3 = _fit_lgbm(
        Xm[train_idx], meta["top3"][train_idx], Xm[valid_idx], meta["top3"][valid_idx]
    )
    model_m1 = _fit_lgbm(
        Xm[train_idx], meta["win"][train_idx], Xm[valid_idx], meta["win"][valid_idx]
    )

    prob3 = model_top3.predict_proba(X[valid_idx])[:, 1]
    prob1 = model_win.predict_proba(X[valid_idx])[:, 1]
    auc_top3 = roc_auc_score(meta["top3"][valid_idx], prob3)
    auc_win = roc_auc_score(meta["win"][valid_idx], prob1)

    mprob3 = model_m3.predict_proba(Xm[valid_idx])[:, 1]
    mprob1 = model_m1.predict_proba(Xm[valid_idx])[:, 1]
    auc_m3 = roc_auc_score(meta["top3"][valid_idx], mprob3)
    auc_m1 = roc_auc_score(meta["win"][valid_idx], mprob1)

    vm = meta[valid_idx].copy()
    vm["prob3"], vm["prob1"] = prob3, prob1
    vm["mprob3"], vm["mprob1"] = mprob3, mprob1
    top1_by3 = vm.sort_values("prob3", ascending=False).groupby("race_id").head(1)
    top1_by1 = vm.sort_values("prob1", ascending=False).groupby("race_id").head(1)
    mtop1_by3 = vm.sort_values("mprob3", ascending=False).groupby("race_id").head(1)
    mtop1_by1 = vm.sort_values("mprob1", ascending=False).groupby("race_id").head(1)
    pop1 = vm[vm["popularity"] == 1]

    # 確率校正: 検証期間の前半で校正器を作り、後半で効果を測る
    half = len(vm) // 2
    cal_idx = vm.index[:half]
    test_idx = vm.index[half:]
    calibrators, ece_before, ece_after = {}, {}, {}
    for key, prob_col, target in [
        ("top3", "prob3", "top3"), ("win", "prob1", "win"),
        ("market_top3", "mprob3", "top3"), ("market_win", "mprob1", "win"),
    ]:
        p_cal = vm.loc[cal_idx, prob_col].values
        y_cal = vm.loc[cal_idx, target].values
        p_test = vm.loc[test_idx, prob_col].values
        y_test = vm.loc[test_idx, target].values
        if len(p_cal) < 100 or len(p_test) < 100:
            continue
        ir = fit_calibrator(y_cal, p_cal)
        before = calibration_error(y_test, p_test)
        after = calibration_error(y_test, ir.predict(p_test))
        ece_before[key] = round(before, 4)
        ece_after[key] = round(after, 4)
        # 校正で悪化するなら適用しない (元から十分校正されているケース)
        if after < before:
            calibrators[key] = ir

    # 複勝オッズ予測モデル (期待値計算用。学習期間だけで学習)
    from .ev import fit_fukusho_odds_model

    fuku_odds_model = fit_fukusho_odds_model(meta, train_idx, db_path)

    importance = sorted(
        zip(FEATURES, model_top3.feature_importances_), key=lambda t: -t[1]
    )

    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model_top3": model_top3,
            "model_win": model_win,
            "model_market_top3": model_m3,
            "model_market_win": model_m1,
            "features": FEATURES,
            "market_features": MARKET_FEATURES,
            "calibrators": calibrators,
            "fuku_odds_model": fuku_odds_model,
            "split_date": str(split_date.date()),
        },
        model_path,
    )

    return {
        "n_train": int(train_idx.sum()),
        "n_valid": int(valid_idx.sum()),
        "n_races_valid": vm["race_id"].nunique(),
        "valid_from": str(split_date.date()),
        "auc_top3": round(float(auc_top3), 4),
        "auc_win": round(float(auc_win), 4),
        "auc_market_top3": round(float(auc_m3), 4),
        "auc_market_win": round(float(auc_m1), 4),
        "model_top1_fukusho_rate": round(float(top1_by3["top3"].mean()), 4),
        "model_top1_win_rate": round(float(top1_by1["win"].mean()), 4),
        "market_top1_fukusho_rate": round(float(mtop1_by3["top3"].mean()), 4),
        "market_top1_win_rate": round(float(mtop1_by1["win"].mean()), 4),
        "ninki1_fukusho_rate": round(float(pop1["top3"].mean()), 4) if len(pop1) else None,
        "ninki1_win_rate": round(float(pop1["win"].mean()), 4) if len(pop1) else None,
        "feature_importance": importance[:10],
        "calibration_ece_before": ece_before,
        "calibration_ece_after": ece_after,
        "model_path": str(model_path),
    }


def main():
    ap = argparse.ArgumentParser(description="予想モデルの学習")
    ap.parse_args()
    metrics = train()
    print("=== 学習結果 ===")
    for k, v in metrics.items():
        if k == "feature_importance":
            print("重要な特徴量 Top10 (top3モデル):")
            for name, imp in v:
                print(f"  {name}: {imp}")
        else:
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
