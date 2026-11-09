"""荒れ度 — 高額配当が出る確率を見積もる。

三連単は pari-mutuel なので、ある組み合わせの配当倍率はおおよそ

    払戻率(0.725) ÷ その組み合わせに投票された割合

になる。投票割合は単勝オッズから作った市場確率を、実際に来る確率は
モデルの勝率を、それぞれ Harville 近似で三連単に広げて求める。
「配当が X 円以上になる組み合わせ」のモデル確率を全部足せば、
そのレースで X 円以上の配当が出る確率になる。

検証期間 (597レース) では、この確率が上位20%のレースで100万超が5.0%
(全体1.5%) 発生し、下位60%では1件も出なかった。
"""
import numpy as np

TAKEOUT = 0.725      # 三連単の払戻率 (控除率27.5%)
BET_UNIT = 100.0     # 1点100円

# 「来る確率」はモデルと市場を半々で混ぜる。実配当3,450レースで比べると
# モデルのみ AUC 0.782 / 市場のみ 0.784 / 半々 0.790 で、半々が最もよかった。
MODEL_WEIGHT = 0.5

# 生の Harville 推定は水準がずれるので、実配当にロジスティック較正をかける。
# (しきい値ごとの (a, b)。較正後の確率 = sigmoid(a * logit(生の確率) + b))
# 2025-07〜2026-07 の3,450レースで推定。検証期間では
#   100万超: 1.84% -> 1.65% (実測 1.67%)、10万超: 16.0% -> 19.0% (実測 20.3%)
CALIBRATION = {
    100_000: (1.1750, 0.5183),
    1_000_000: (1.5415, 1.8882),
}

# 頭数ごとの (i,j,k) 添字は使い回せるので作り置きする
_PERM_CACHE = {}


def _perms(n):
    """n頭から3頭を選ぶ順列の添字 (i, j, k) を返す。"""
    if n not in _PERM_CACHE:
        a = np.arange(n)
        i, j, k = np.meshgrid(a, a, a, indexing="ij")
        ok = (i != j) & (j != k) & (i != k)
        _PERM_CACHE[n] = (i[ok], j[ok], k[ok])
    return _PERM_CACHE[n]


def trifecta_probs(p):
    """勝率 p (合計1) から三連単の全組み合わせ確率を返す。

    Harville 近似: 1着が抜けた残りで2着を、さらに抜けた残りで3着を選ぶ。
    """
    p = np.asarray(p, dtype=float)
    i, j, k = _perms(len(p))
    rem_i = 1.0 - p[i]
    rem_j = rem_i - p[j]
    # 残りが0以下になる組み合わせ (確率1の馬がいる等) は捨てる
    valid = (rem_i > 1e-9) & (rem_j > 1e-9)
    out = np.zeros(len(i))
    out[valid] = (
        p[i][valid] * (p[j][valid] / rem_i[valid]) * (p[k][valid] / rem_j[valid])
    )
    return out


def _normalize(x):
    x = np.asarray(x, dtype=float)
    total = x.sum()
    return x / total if total > 0 else x


def _calibrate(p, threshold):
    """生の推定を実配当に合わせる (ロジスティック較正)。"""
    ab = CALIBRATION.get(threshold)
    if ab is None:
        return p
    a, b = ab
    p = min(max(p, 1e-6), 1 - 1e-6)
    return float(1.0 / (1.0 + np.exp(-(a * np.log(p / (1 - p)) + b))))


def payout_probs(model_p, odds, thresholds=(100_000, 1_000_000)):
    """各しきい値以上の配当が出る確率と、推定の最高配当を返す。

    model_p: モデルの勝率 (レース内で正規化されていなくてよい)
    odds:    単勝オッズ
    """
    model_p = np.asarray(model_p, dtype=float)
    odds = np.asarray(odds, dtype=float)
    ok = np.isfinite(model_p) & np.isfinite(odds) & (odds > 0)
    if ok.sum() < 4:
        return {t: None for t in thresholds} | {"max_payout": None}

    market = _normalize(1.0 / odds[ok])
    # 的中側はモデルと市場の混合。配当側は投票割合そのものなので市場だけ
    blend = _normalize(MODEL_WEIGHT * _normalize(model_p[ok])
                       + (1 - MODEL_WEIGHT) * market)
    q_outcome = trifecta_probs(blend)
    q_market = trifecta_probs(market)

    payout = TAKEOUT / np.maximum(q_market, 1e-12) * BET_UNIT
    out = {t: _calibrate(float(q_outcome[payout >= t].sum()), t)
           for t in thresholds}

    # 「来る可能性がそれなりにある組み合わせ」での最高配当。
    # 万馬券の上限は青天井なので、確率の薄すぎる組み合わせは除く。
    plausible = q_outcome >= 1e-4
    out["max_payout"] = (
        float(payout[plausible].max()) if plausible.any() else None
    )
    return out


def label(p_1m):
    """100万超の確率を人が読める荒れ度にする。全体平均は約1.4%。"""
    if p_1m is None:
        return None
    if p_1m >= 0.030:
        return "大荒れ警戒"
    if p_1m >= 0.018:
        return "荒れ気味"
    if p_1m >= 0.008:
        return "標準"
    return "堅い"
