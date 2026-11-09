"""予想根拠の説明。

LightGBM 内蔵の SHAP 値 (pred_contrib) で「どの特徴量が確率を
押し上げた/押し下げたか」を馬ごとに算出する。寄与はログオッズ空間の値。
"""
import numpy as np
import pandas as pd

FEATURE_LABELS = {
    "waku": "枠番", "horse_no": "馬番", "field_size": "頭数", "age": "年齢",
    "sex_code": "性別", "kinryo": "斤量", "weight": "馬体重", "weight_diff": "馬体重増減",
    "distance": "距離", "surface_code": "芝/ダート", "going_code": "馬場状態",
    "place_code": "競馬場", "is_nar": "中央/地方",
    "career_starts": "通算出走数", "career_win_rate": "通算勝率",
    "career_top3_rate": "通算複勝率", "avg_finish5": "近5走平均着順",
    "last_finish": "前走着順", "days_since_last": "レース間隔(日)",
    "jockey_starts": "騎手のDB内騎乗数", "jockey_top3_rate": "騎手複勝率",
    "avg_corner1_ratio5": "脚質(先行度: 小=前)", "avg_agari5": "近5走平均上り3F",
    "sire_top3_rate": "父産駒の複勝率", "dam_sire_top3_rate": "母父産駒の複勝率",
    "course_inner_adv": "コースの内枠有利度(攻略表)",
    "course_front_adv": "コースの前有利度(攻略表)",
    "waku_fit": "枠とコースの適合度",
    "style_fit": "脚質とコースの適合度",
    "turn": "回り方向",
    "straight_len": "直線の長さ(m)",
    "slope_level": "坂のきつさ",
    "turf_yoshiba": "洋芝コース",
    "place_top3_rate": "この競馬場での複勝率",
    "same_turn_top3_rate": "同じ回り方向での複勝率",
    "dist_band_top3_rate": "同距離帯での複勝率",
    "dist_change": "前走からの距離変更(m)",
    "form_trend": "調子の傾き(前走-近5走平均)",
    "front_count": "逃げ・先行候補の数",
    "pace_pressure_self": "ペース圧(先行馬×逃げ候補数)",
    "workout_time4f": "坂路4F追い切りタイム(秒)",
    "workout_lap1f": "坂路ラスト1Fタイム(秒)",
    "workout_count14d": "直近2週の坂路調教本数",
}

RATE_FEATURES = {
    "career_win_rate", "career_top3_rate", "jockey_top3_rate",
    "sire_top3_rate", "dam_sire_top3_rate", "avg_corner1_ratio5",
    "place_top3_rate", "same_turn_top3_rate", "dist_band_top3_rate",
}

# カテゴリ系特徴量はコードではなく名前で表示する
def _category_maps():
    from .scraper import PLACES

    return {
        "place_code": {int(k): v for k, v in PLACES.items()},
        "sex_code": {0: "牡", 1: "牝", 2: "セン"},
        "surface_code": {0: "芝", 1: "ダート", 2: "障害"},
        "going_code": {0: "良", 1: "稍重", 2: "重", 3: "不良"},
        "is_nar": {0: "中央", 1: "地方"},
        "turn": {0: "右回り", 1: "左回り"},
        "turf_yoshiba": {0: "野芝等", 1: "洋芝"},
    }


def contributions(model, X):
    """全馬の特徴量寄与 (SHAP値)。返り値 shape = (n_horses, n_features)"""
    raw = model.booster_.predict(X.values.astype(float), pred_contrib=True)
    return raw[:, :-1]  # 最終列はベースライン(全体平均)なので除く


def _fmt_value(feat, val):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "データなし"
    cat = _category_maps().get(feat)
    if cat is not None:
        return cat.get(int(val), str(int(val)))
    if feat in RATE_FEATURES:
        return f"{val:.0%}"
    if float(val).is_integer():
        return str(int(val))
    return f"{val:.2f}"


def explain_table(X, contribs, entry_idx):
    """1頭分の説明テーブル。寄与の大きい順 (プラス→マイナス)。"""
    row = X.iloc[entry_idx]
    c = contribs[entry_idx]
    df = pd.DataFrame({
        "特徴量": [FEATURE_LABELS.get(f, f) for f in X.columns],
        "この馬の値": [_fmt_value(f, row[f]) for f in X.columns],
        "寄与": np.round(c, 3),
    })
    return df.sort_values("寄与", ascending=False).reset_index(drop=True)


def summary_text(exp_df, horse_name):
    """説明テーブルから 1〜2文の要約を作る。"""
    plus = exp_df[exp_df["寄与"] > 0.02]
    minus = exp_df[exp_df["寄与"] < -0.02].sort_values("寄与")
    parts = []
    if len(plus):
        items = "、".join(
            f"{r['特徴量']}({r['この馬の値']})" for _, r in plus.head(3).iterrows()
        )
        parts.append(f"**{horse_name}** の評価を押し上げたのは {items}。")
    if len(minus):
        items = "、".join(
            f"{r['特徴量']}({r['この馬の値']})" for _, r in minus.head(3).iterrows()
        )
        parts.append(f"評価を下げたのは {items}。")
    if not parts:
        parts.append(f"**{horse_name}** は突出した強調材料・不安材料がなく、平均的な評価です。")
    return " ".join(parts)
