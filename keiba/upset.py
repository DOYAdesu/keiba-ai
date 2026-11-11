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


# 較正に足す説明変数: 頭数と、支持の偏り (ハーフィンダール sum(p^2))。
# logit(生の確率) だけだと上端が潰れて、本当に荒れるレースを言い当てられない。
# 運用と同じやり方 (過去全部で合わせて次の3か月を予想) を16四半期ぶん回した
# 期外での実測:
#            オッズ前              オッズ込み
#   上位1%   5.5%→9.0% (実測 5.6%→8.8%)   7.2%→9.6% (実測 8.5%→10.8%)
#   AUC      0.655→0.690                  0.692→0.695
# オッズ込みは全体のAUCではほとんど変わらないが、「推定8%以上」と言える
# レースが11件から140件に増え、そこで実際に10.7%出る。上端の見分けが付く。
CALIB_FEATURES = ("ln_field", "ln_herf")

# 説明変数を足すしきい値。10万超 (発生率21%) では上位1%で推定63%・実測54%と
# 出しすぎになり、上位5%の改善 (47.4%->50.6%) に見合わないので足さない。
CALIB_FEATURE_THRESHOLDS = (1_000_000,)

# 標準化したあとの説明変数にかける罰則。1.0 までは期外の成績が変わらず、
# 10 にすると上端が鈍る (オッズ込みの上位1%が 9.3% -> 8.4%)。
CALIB_L2_FEATURES = 1.0


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -40.0, 40.0)))


def _coefs(entry):
    """較正の係数を (係数, 説明変数名, 平均, 標準偏差) に揃える。

    [a, b] の2つだけなら昔の形 (logit だけ)。
    {"coef": [...], "features": [...], "mean": [...], "std": [...]} なら
    説明変数つき (値は mean/std で標準化してから使う)。
    """
    if entry is None:
        return None, (), (), ()
    if isinstance(entry, dict):
        names = tuple(entry.get("features") or ())
        return (list(entry.get("coef") or ()), names,
                list(entry.get("mean") or [0.0] * len(names)),
                list(entry.get("std") or [1.0] * len(names)))
    return list(entry), (), (), ()


def calibrate(p, threshold, calib=None, feats=None):
    """生の推定を実配当に合わせる (ロジスティック較正)。

    calib は {しきい値: 係数}。しきい値のキーは文字列でも数値でもよい
    (site_meta に JSON で入れると文字列になる)。
    feats は {"ln_field": .., "ln_herf": ..} のような説明変数。
    係数が説明変数を要求しているのに渡されていなければ較正しない
    (出すぎた数字を出すより、生の値のままの方が害が小さい)。
    """
    if p is None:
        return None
    table = CALIBRATION if calib is None else calib
    coef, names, mean, std = _coefs(
        table.get(threshold, table.get(str(threshold))))
    if not coef:
        return p
    z = coef[0] * float(_logit(p)) + coef[-1]
    for name, w, m, sd in zip(names, coef[1:-1], mean, std):
        v = None if feats is None else feats.get(name)
        if v is None or not np.isfinite(v):
            return p
        z += w * (float(v) - m) / (sd or 1.0)
    return float(_sigmoid(z))


def _irls(X, y, weight=None, l2=1e-4, l2_extra=None, iters=100):
    """重みつきロジスティック回帰 (IRLS)。X は切片を含まない。

    1列目 (logit) には l2、2列目以降 (説明変数) には l2_extra の罰則をかける。
    説明変数は生の推定と強く相関しているので、少し押さえておく。
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    sw = np.ones(len(y)) if weight is None else np.asarray(weight, dtype=float)
    A = np.column_stack([X, np.ones(len(X))])
    w = np.zeros(A.shape[1])
    pen = np.zeros((A.shape[1], A.shape[1]))
    pen[0, 0] = l2
    for i in range(1, A.shape[1] - 1):
        pen[i, i] = l2 if l2_extra is None else l2_extra
    for _ in range(iters):
        p = _sigmoid(A @ w)
        g = A.T @ (sw * (p - y)) + pen @ w
        s = np.clip(sw * p * (1 - p), 1e-12, None)
        H = A.T @ (A * s[:, None]) + pen + 1e-9 * np.eye(A.shape[1])
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-10:
            break
    return w


def fit_calibration(raw, hit, feats=None, names=()):
    """生の確率 (と説明変数) から実際の当たり外れへの写像を求める。

    raw:   生の確率
    hit:   実際に当たったか (0/1)
    feats: {名前: 配列} の説明変数。names に使う順で名前を並べる

    返り値は {"coef": [logitの傾き, 説明変数の傾き..., 切片],
             "features": [名前...], "mean": [...], "std": [...]}。
    名前が無ければ昔と同じ [a, b] を返す。

    水準は全期間で合わせてよい (2020年の発生率2.78%は2021年以降の2.0〜2.2%
    より高いが、説明変数を標準化して罰則をかければ期外の平均のずれは
    +0.10pt に収まる。直近だけで切片を合わせ直すと、その年のばらつきを
    拾って逆に出し渋る)。
    """
    names = tuple(names)
    cols = [_logit(raw)]
    mean, std = [], []
    for name in names:
        v = np.asarray(feats[name], dtype=float)
        # 説明変数は標準化する。生の推定と単位がまるで違うため、そのままだと
        # 係数と切片が極端な値で釣り合い (ln_field=-7.07 / 切片+23.1)、
        # 学習期間の外に出たときに暴れやすい。
        m, sd = float(v.mean()), float(v.std())
        sd = sd if sd > 1e-9 else 1.0
        mean.append(m)
        std.append(sd)
        cols.append((v - m) / sd)
    X = np.column_stack(cols)
    y = np.asarray(hit, dtype=float)
    w = _irls(X, y, l2_extra=CALIB_L2_FEATURES if names else None)
    if not names:
        return [float(w[0]), float(w[1])]
    return {"coef": [float(v) for v in w], "features": list(names),
            "mean": mean, "std": std}


def payout_probs(model_p, odds, thresholds=(100_000, 1_000_000), calib=None,
                 with_payout=True):
    """各しきい値以上の配当が出る確率と、推定の最高配当を返す。

    model_p: モデルの勝率 (レース内で正規化されていなくてよい)
    odds:    単勝オッズ
    calib:   {しきい値: (a, b)} の較正係数。省略すると CALIBRATION
    with_payout: False にすると最高配当を出さない (オッズを作り物で
             埋めている場合、最高配当は「打ち切りの位置」しか表さないので)

    raw_<しきい値> に較正前の生の確率も入れて返す (較正を学習し直すのに使う)。
    """
    model_p = np.asarray(model_p, dtype=float)
    odds = np.asarray(odds, dtype=float)
    ok = np.isfinite(model_p) & np.isfinite(odds) & (odds > 0)
    if ok.sum() < 4:
        return ({t: None for t in thresholds} | {"max_payout": None}
                | {f"raw_{t}": None for t in thresholds}
                | {f"f_{k}": None for k in CALIB_FEATURES})

    market = _normalize(1.0 / odds[ok])
    # 的中側はモデルと市場の混合。配当側は投票割合そのものなので市場だけ
    blend = _normalize(MODEL_WEIGHT * _normalize(model_p[ok])
                       + (1 - MODEL_WEIGHT) * market)
    q_outcome = trifecta_probs(blend)
    q_market = trifecta_probs(market)

    payout = TAKEOUT / np.maximum(q_market, 1e-12) * BET_UNIT
    raw = {t: float(q_outcome[payout >= t].sum()) for t in thresholds}
    # 較正に足す説明変数 (CALIB_FEATURES)。頭数と、支持がどれだけ偏って
    # いるか (ハーフィンダール sum(p^2))。生の推定だけでは拾えない。
    feats = {
        "ln_field": float(np.log(len(market))),
        "ln_herf": float(np.log(max(float((market ** 2).sum()), 1e-4))),
    }
    out = {t: calibrate(raw[t], t, calib, feats) for t in thresholds}
    out |= {f"raw_{t}": raw[t] for t in thresholds}
    out |= {f"f_{k}": v for k, v in feats.items()}

    # 「来る可能性がそれなりにある組み合わせ」での最高配当。
    # 万馬券の上限は青天井なので、確率の薄すぎる組み合わせは除く。
    plausible = q_outcome >= 1e-4
    out["max_payout"] = (
        float(payout[plausible].max())
        if with_payout and plausible.any() else None
    )
    return out


# 荒れ度のいちばん上の段。較正に頭数と集中度を足して上端が伸びたので
# (推定5.5%以上のレースで実際に8〜11%出る)、「大荒れ警戒」の上を分けて
# 出せるようにした。これが無いと、オッズ発表前の特別戦はほとんど
# 「大荒れ警戒」で埋まってしまい、本当に荒れるレースが埋もれる。
UPSET_TOP_LABEL = "特大荒れ"


def label(p_1m):
    """100万超の確率を人が読める荒れ度にする。全レースの平均は約2.2%。"""
    if p_1m is None:
        return None
    if p_1m >= 0.055:
        return UPSET_TOP_LABEL
    if p_1m >= 0.030:
        return "大荒れ警戒"
    if p_1m >= 0.018:
        return "荒れ気味"
    if p_1m >= 0.008:
        return "標準"
    return "堅い"
