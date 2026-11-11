"""出走馬の顔ぶれ (成績・タイム) から見た「荒れやすさ」。

荒れ度 (100万超の確率) はオッズとAIの勝率から三連単に広げて見積もっているが、
それだけでは拾えない顔ぶれの影響がある (scripts/upset_fields.py で確認):
  - 能力指数80以上の馬が少ない (抜けた実力馬がいない) ほど荒れる
  - 走破タイムの上位3頭が拮抗しているほど荒れる
  - 前走3着内の馬 (調子の良い馬) が多いほど荒れる
  - 逃げ馬が2頭以上いる (先行争い) と荒れる
頭数をそろえても (16頭以上だけで見ても) 向きは変わらない。
これらをロジスティック回帰で荒れ度に足す (学習期間で係数を決める)。
"""
import numpy as np
import pandas as pd

FIELD_COLS = ["n_strong", "spd_gap", "n_good_last", "n_nige", "fs"]


def race_fields(h):
    """1頭1行 (race_id, ability_idx, c_spd, last_finish, style_ratio, p_win, odds) → 1レース1行。"""
    def one(g):
        spd = pd.to_numeric(g.get("c_spd"), errors="coerce").dropna().sort_values(ascending=False)
        odds = pd.to_numeric(g.get("odds_for_upset", g.get("odds")), errors="coerce").dropna()
        return pd.Series({
            "n_strong": float((pd.to_numeric(g["ability_idx"], errors="coerce") >= 80).sum()),
            "spd_gap": float(spd.iloc[0] - spd.iloc[2]) if len(spd) >= 3 else np.nan,
            "n_good_last": float((pd.to_numeric(g.get("last_finish"), errors="coerce") <= 3).sum()),
            "n_nige": float((pd.to_numeric(g.get("style_ratio"), errors="coerce") <= 0.15).sum()),
            "fs": float(len(g)),
            "fav": float(odds.min()) if len(odds) else np.nan,
            "top_ai": float(pd.to_numeric(g["p_win"], errors="coerce").max()),
        })
    return h.groupby("race_id", sort=False).apply(one)


def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 0.5)
    return np.log(p / (1 - p))


def fit(p_hit, fields, hit, mask):
    """学習期間 (mask) で係数を決める。返り値: {"cols", "coef", "intercept", "mean", "std"}"""
    from sklearn.linear_model import LogisticRegression

    F = fields[FIELD_COLS].astype(float)
    ok = mask & F.notna().all(axis=1).to_numpy() & ~np.isnan(np.asarray(p_hit, float)) \
        & ~np.isnan(np.asarray(hit, float))
    if ok.sum() < 2000 or np.nansum(np.asarray(hit, float)[ok]) < 30:
        return None
    mean, std = F[ok].mean(), F[ok].std().replace(0, 1)
    Z = np.column_stack([_logit(np.asarray(p_hit)[ok]), ((F[ok] - mean) / std).to_numpy()])
    m = LogisticRegression(max_iter=1000, C=1.0).fit(Z, np.asarray(hit, float)[ok])
    return {"cols": FIELD_COLS, "coef": [round(float(c), 5) for c in m.coef_[0]],
            "intercept": round(float(m.intercept_[0]), 5),
            "mean": [round(float(v), 4) for v in mean], "std": [round(float(v), 4) for v in std]}


def apply(p_hit, fields, model):
    """荒れ度に顔ぶれを足した確率。足せない行 (値が欠ける・モデルなし) は元のまま。"""
    p = np.asarray(p_hit, float)
    if not model:
        return p
    F = fields[model["cols"]].astype(float).to_numpy()
    Z = (F - np.array(model["mean"])) / np.array(model["std"])
    z = model["intercept"] + model["coef"][0] * _logit(p) + Z @ np.array(model["coef"][1:])
    out = 1 / (1 + np.exp(-z))
    bad = np.isnan(p) | np.isnan(Z).any(axis=1)
    return np.where(bad, p, out)


def to_json(r):
    """サイトに出す1レースの顔ぶれ (小さな JSON 文字列)。"""
    import json
    d = {"strong": r.get("n_strong"), "gap": r.get("spd_gap"), "good": r.get("n_good_last"),
         "nige": r.get("n_nige")}
    d = {k: (round(float(v), 1) if k == "gap" else int(v)) for k, v in d.items()
         if v is not None and v == v}
    return json.dumps(d, separators=(",", ":")) if d else None
