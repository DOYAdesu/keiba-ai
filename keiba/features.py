"""レース結果 DB から機械学習用の特徴量を作る。

ターゲットは「3着以内(複勝圏内)」と「1着(勝利)」の2つ。
各行の特徴量には、そのレースより前の情報だけを使う(リーク防止)。
"""
import re
import sqlite3

import numpy as np
import pandas as pd

from .scraper import DB_PATH, connect

SEX_MAP = {"牡": 0, "牝": 1, "セ": 2, "せん": 2, "騙": 2}
SURFACE_MAP = {"芝": 0, "ダ": 1, "障": 2}
GOING_MAP = {"良": 0, "稍重": 1, "重": 2, "不良": 3}

FEATURES = [
    "waku", "horse_no", "field_size", "age", "sex_code", "kinryo",
    "weight", "weight_diff", "distance", "surface_code", "going_code",
    "place_code", "is_nar",
    "career_starts", "career_win_rate", "career_top3_rate",
    "avg_finish5", "last_finish", "days_since_last",
    "jockey_starts", "jockey_top3_rate",
    # 脚質・末脚 (通過順位・上り3F から)
    "avg_corner1_ratio5", "avg_agari5",
    # 血統 (産駒成績)
    "sire_top3_rate", "dam_sire_top3_rate",
    # コース攻略表 (JRA): コースのバイアスと馬の枠・脚質の適合度
    "course_inner_adv", "course_front_adv", "waku_fit", "style_fit",
    # コース構造の数値化 (競馬辞典 第12章 §6)
    "turn", "straight_len", "slope_level", "turf_yoshiba",
    # 適性: 同競馬場・同回り・同距離帯の複勝率 (第12章 §4)
    "place_top3_rate", "same_turn_top3_rate", "dist_band_top3_rate",
    # ローテ・フォーム・ペース圧 (第12章 §5)
    "dist_change", "form_trend", "front_count", "pace_pressure_self",
    # 調教 (坂路): 直前の追い切りタイムと本数
    "workout_time4f", "workout_lap1f", "workout_count14d",
]

# 市場情報 (オッズ) を加えた特徴量。的中率重視の「市場モデル」で使う。
# 競馬辞典 第12章 §3 の「能力モデルと市場モデルを分ける」に沿った構成。
MARKET_FEATURES = FEATURES + ["log_odds", "market_prob", "odds_rank"]


def add_market_features(X, odds, race_ids):
    """特徴量に市場情報を足す。odds: Series (単勝オッズ)。"""
    X = X.copy()
    # 出馬表の取得元によっては object 型で来るため必ず数値化する
    o = pd.to_numeric(pd.Series(odds, index=X.index), errors="coerce").replace(0, np.nan)
    X["log_odds"] = np.log(o)
    inv = 1.0 / o
    grp = pd.Series(race_ids, index=X.index)
    X["market_prob"] = inv / inv.groupby(grp).transform("sum")
    X["odds_rank"] = o.groupby(grp).rank(method="min")
    return X[MARKET_FEATURES]


def load_results(db_path=DB_PATH):
    conn = connect(db_path)  # スキーマのマイグレーションも兼ねる
    try:
        df = pd.read_sql("SELECT * FROM results", conn)
    finally:
        conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df


def load_horses(db_path=DB_PATH):
    conn = connect(db_path)
    try:
        return pd.read_sql("SELECT * FROM horses", conn)
    finally:
        conn.close()


def load_workouts(db_path=DB_PATH):
    conn = connect(db_path)
    try:
        df = pd.read_sql("SELECT * FROM workouts", conn)
    finally:
        conn.close()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def add_workout_features(df, workouts=None):
    """レース直前 (14日以内) の坂路調教を特徴量にする。"""
    cols = ["workout_time4f", "workout_lap1f", "workout_count14d"]
    if workouts is None:
        workouts = load_workouts()
    if workouts.empty:
        for c in cols:
            df[c] = np.nan
        return df

    w = workouts.sort_values("date")
    # 各出走行に、その日より前の直近の調教を対応づける
    left = df[["horse_id", "date"]].copy()
    left["_row"] = np.arange(len(left))
    left = left.sort_values("date")
    merged = pd.merge_asof(
        left, w, left_on="date", right_on="date", by="horse_id",
        direction="backward", allow_exact_matches=False,
        tolerance=pd.Timedelta("28D"),
    ).sort_values("_row")
    df["workout_time4f"] = merged["time4f"].values
    df["workout_lap1f"] = merged["lap1f"].values

    # 直前14日の本数
    counts = []
    by_horse = {k: v["date"].values for k, v in w.groupby("horse_id")}
    for hid, d in zip(df["horse_id"], df["date"]):
        dates = by_horse.get(hid)
        if dates is None:
            counts.append(np.nan)
            continue
        lo = np.datetime64(d - pd.Timedelta("14D"))
        hi = np.datetime64(d)
        counts.append(int(((dates >= lo) & (dates < hi)).sum()))
    df["workout_count14d"] = counts
    return df


def normalize_bloodline(name):
    """血統名の表記ゆれを吸収する。

    netkeiba「ハービンジャーHarbinger(英)」と JRA-VAN「ハービンジャー」を
    同一キーにするため、括弧書きと英数字を除去する。
    """
    if not isinstance(name, str) or not name:
        return np.nan
    n = re.sub(r"[\(（].*?[\)）]", "", name)
    n = re.sub(r"[A-Za-z0-9'’\.\-\s]+", "", n)
    return n if n else np.nan


def attach_pedigree(df, horses):
    """results 行に父・母父の正規化キーを付与する (netkeiba/JRA-VAN 共通)。"""
    if horses is None or horses.empty:
        df["sire_key"] = np.nan
        df["dam_sire_key"] = np.nan
        return df
    m = horses.set_index("horse_id")
    df["sire_key"] = df["horse_id"].map(m["sire_name"]).map(normalize_bloodline)
    df["dam_sire_key"] = df["horse_id"].map(m["dam_sire_name"]).map(normalize_bloodline)
    return df


def add_history_features(df, horses=None):
    """過去成績ベースの特徴量を追加する(学習用・全行一括)。"""
    df = df.sort_values(["date", "race_id", "horse_no"]).reset_index(drop=True)
    df["win"] = (df["finish"] == 1).astype(int)
    df["top3"] = (df["finish"] <= 3).astype(int)
    df["field_size"] = df.groupby("race_id")["horse_no"].transform("count")
    df["corner1_ratio"] = df["corner1"] / df["field_size"]

    g = df.groupby("horse_id", sort=False)
    df["career_starts"] = g.cumcount()
    wins_before = g["win"].cumsum() - df["win"]
    top3_before = g["top3"].cumsum() - df["top3"]
    starts = df["career_starts"].replace(0, np.nan)
    df["career_win_rate"] = wins_before / starts
    df["career_top3_rate"] = top3_before / starts
    df["last_finish"] = g["finish"].shift()
    df["avg_finish5"] = g["finish"].transform(
        lambda s: s.shift().rolling(5, min_periods=1).mean()
    )
    df["days_since_last"] = (df["date"] - g["date"].shift()).dt.days
    df["avg_corner1_ratio5"] = g["corner1_ratio"].transform(
        lambda s: s.shift().rolling(5, min_periods=1).mean()
    )
    df["avg_agari5"] = g["agari"].transform(
        lambda s: s.shift().rolling(5, min_periods=1).mean()
    )

    gj = df.groupby("jockey_id", sort=False)
    df["jockey_starts"] = gj.cumcount()
    j_top3_before = gj["top3"].cumsum() - df["top3"]
    j_starts = df["jockey_starts"].replace(0, np.nan)
    df["jockey_top3_rate"] = j_top3_before / j_starts

    # 血統: 産駒(父・母父単位)のそれまでの複勝率
    df = attach_pedigree(df, horses if horses is not None else load_horses())
    for col, out in [("sire_key", "sire_top3_rate"), ("dam_sire_key", "dam_sire_top3_rate")]:
        gs = df.groupby(col, sort=False, dropna=True)
        s_starts = gs.cumcount()
        s_top3_before = gs["top3"].cumsum() - df["top3"]
        df[out] = s_top3_before / s_starts.replace(0, np.nan).astype(float)
        df.loc[df[col].isna(), out] = np.nan

    df["sex_code"] = df["sex"].map(SEX_MAP)
    df["surface_code"] = df["surface"].map(SURFACE_MAP)
    df["going_code"] = df["going"].map(GOING_MAP)
    df["place_code"] = df["race_id"].str[4:6].astype(int)

    # コース攻略表 (JRA のみ。表に無いコースは NaN)
    from .course_bias import course_scores

    scores = [
        course_scores(rid[4:6], s, d)
        for rid, s, d in zip(df["race_id"], df["surface"], df["distance"])
    ]
    df["course_inner_adv"] = [t[0] for t in scores]
    df["course_front_adv"] = [t[1] for t in scores]
    waku_pos = (df["waku"] - 1) / 7.0
    df["waku_fit"] = df["course_inner_adv"] * (1 - 2 * waku_pos)
    df["style_fit"] = df["course_front_adv"] * (1 - 2 * df["avg_corner1_ratio5"])

    # コース構造の数値化 (第12章 §6)
    from .course_geo import geo_features

    geos = [
        geo_features(rid[4:6], s, d)
        for rid, s, d in zip(df["race_id"], df["surface"], df["distance"])
    ]
    df["turn"] = [t[0] for t in geos]
    df["straight_len"] = [t[1] for t in geos]
    df["slope_level"] = [t[2] for t in geos]
    df["turf_yoshiba"] = [t[3] for t in geos]

    # 適性: 同競馬場・同回り・同距離帯のそれまでの複勝率 (リーク防止のため累積-当該)
    def _grouped_rate(keys, out):
        gg = df.groupby(keys, sort=False, dropna=True)
        starts = gg.cumcount()
        before = gg["top3"].cumsum() - df["top3"]
        df[out] = before / starts.replace(0, np.nan)

    df["dist_band"] = df["distance"] // 400
    _grouped_rate(["horse_id", "place_code"], "place_top3_rate")
    _grouped_rate(["horse_id", "turn"], "same_turn_top3_rate")
    _grouped_rate(["horse_id", "dist_band"], "dist_band_top3_rate")

    # ローテ・フォーム
    g2 = df.groupby("horse_id", sort=False)
    df["dist_change"] = df["distance"] - g2["distance"].shift()
    df["form_trend"] = df["last_finish"] - df["avg_finish5"]

    # ペース圧: 逃げ・先行候補 (先行度<0.3) の数と自分の脚質の掛け合わせ
    is_front = (df["avg_corner1_ratio5"] < 0.3).astype(int)
    df["front_count"] = is_front.groupby(df["race_id"]).transform("sum")
    df["pace_pressure_self"] = df["front_count"] * (0.5 - df["avg_corner1_ratio5"])

    df = add_workout_features(df)
    return df


def build_training_data(df, horses=None):
    df = add_history_features(df, horses)
    X = df[FEATURES].astype(float)
    return X, df


class History:
    """予想時に参照する過去成績。馬・騎手・血統ごとに事前索引化して高速に引く。"""

    def __init__(self, df, horses, workouts=None):
        self.df = df
        self.horses = horses
        self.workouts = workouts if workouts is not None else pd.DataFrame()
        self._by_workout = (
            {k: v.sort_values("date") for k, v in self.workouts.groupby("horse_id")}
            if not self.workouts.empty else {}
        )
        self._by_horse = {k: v for k, v in df.groupby("horse_id", sort=False)}
        self._by_jockey = {k: v for k, v in df.groupby("jockey_id", sort=False)}
        self._by_sire = {k: v for k, v in df.groupby("sire_key", sort=False)}
        self._by_dam_sire = {k: v for k, v in df.groupby("dam_sire_key", sort=False)}
        self._ped = (
            horses.set_index("horse_id") if horses is not None and not horses.empty
            else None
        )

    def horse(self, horse_id):
        return self._by_horse.get(horse_id)

    def jockey(self, jockey_id):
        return self._by_jockey.get(jockey_id)

    def sire(self, key):
        return self._by_sire.get(key)

    def dam_sire(self, key):
        return self._by_dam_sire.get(key)

    def workout_stats(self, horse_id, as_of):
        """予想時に使う: 直近の坂路調教。"""
        out = {"workout_time4f": np.nan, "workout_lap1f": np.nan,
               "workout_count14d": np.nan}
        w = self._by_workout.get(horse_id)
        if w is None:
            return out
        past = w[w["date"] < as_of]
        if past.empty:
            return out
        recent = past.iloc[-1]
        if (as_of - recent["date"]).days <= 28:
            out["workout_time4f"] = recent["time4f"]
            out["workout_lap1f"] = recent["lap1f"]
        out["workout_count14d"] = int(
            (past["date"] >= as_of - pd.Timedelta("14D")).sum()
        )
        return out

    def pedigree_of(self, horse_id):
        """(父名, 母父名) を返す。未登録なら (None, None)。"""
        if self._ped is None or horse_id not in self._ped.index:
            return None, None
        row = self._ped.loc[horse_id]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        return row.get("sire_name"), row.get("dam_sire_name")


def prepare_history(db_path=DB_PATH):
    """予想時に参照する過去成績を用意する。返り値: (History, horses DataFrame)"""
    from .course_geo import turn_of

    df = load_results(db_path)
    horses = load_horses(db_path)
    df["field_size"] = df.groupby("race_id")["horse_no"].transform("count")
    df["corner1_ratio"] = df["corner1"] / df["field_size"]
    df["place_code_str"] = df["race_id"].str[4:6]
    df["turn"] = df["place_code_str"].map(turn_of)
    df["dist_band"] = df["distance"] // 400
    df = attach_pedigree(df, horses)
    return History(df, horses, load_workouts(db_path)), horses


def horse_stats(hist, horse_id, as_of):
    """予想時に使う: as_of より前のその馬の成績サマリ。"""
    sub = hist.horse(horse_id)
    h = sub[sub["date"] < as_of].sort_values("date") if sub is not None else None
    n = 0 if h is None else len(h)
    if n == 0:
        return {
            "career_starts": 0, "career_win_rate": np.nan,
            "career_top3_rate": np.nan, "avg_finish5": np.nan,
            "last_finish": np.nan, "days_since_last": np.nan,
            "avg_corner1_ratio5": np.nan, "avg_agari5": np.nan,
        }
    last5 = h.tail(5)
    return {
        "career_starts": n,
        "career_win_rate": (h["finish"] == 1).mean(),
        "career_top3_rate": (h["finish"] <= 3).mean(),
        "avg_finish5": last5["finish"].mean(),
        "last_finish": h["finish"].iloc[-1],
        "days_since_last": (as_of - h["date"].iloc[-1]).days,
        "avg_corner1_ratio5": last5["corner1_ratio"].mean(),
        "avg_agari5": last5["agari"].mean(),
    }


def jockey_stats(hist, jockey_id, as_of):
    sub = hist.jockey(jockey_id)
    j = sub[sub["date"] < as_of] if sub is not None else None
    n = 0 if j is None else len(j)
    return {
        "jockey_starts": n,
        "jockey_top3_rate": (j["finish"] <= 3).mean() if n else np.nan,
    }


def aptitude_stats(hist, horse_id, as_of, place_code_str, turn, distance):
    """予想時に使う: 同競馬場・同回り・同距離帯の複勝率と距離変更幅。"""
    out = {
        "place_top3_rate": np.nan, "same_turn_top3_rate": np.nan,
        "dist_band_top3_rate": np.nan, "dist_change": np.nan,
    }
    sub = hist.horse(horse_id)
    h = sub[sub["date"] < as_of] if sub is not None else None
    if h is None or h.empty:
        return out
    hp = h[h["place_code_str"] == place_code_str]
    if len(hp):
        out["place_top3_rate"] = (hp["finish"] <= 3).mean()
    if turn is not None and not (isinstance(turn, float) and np.isnan(turn)):
        ht = h[h["turn"] == turn]
        if len(ht):
            out["same_turn_top3_rate"] = (ht["finish"] <= 3).mean()
    if distance:
        hd = h[h["dist_band"] == int(distance) // 400]
        if len(hd):
            out["dist_band_top3_rate"] = (hd["finish"] <= 3).mean()
        last_dist = h.sort_values("date")["distance"].iloc[-1]
        if not pd.isna(last_dist):
            out["dist_change"] = int(distance) - last_dist
    return out


def sire_stats(hist, horses, horse_id, as_of):
    """予想対象馬の父・母父の産駒複勝率 (as_of より前)。"""
    out = {"sire_top3_rate": np.nan, "dam_sire_top3_rate": np.nan}
    sire_name, dam_sire_name = hist.pedigree_of(horse_id)
    for name, getter, key in [
        (sire_name, hist.sire, "sire_top3_rate"),
        (dam_sire_name, hist.dam_sire, "dam_sire_top3_rate"),
    ]:
        k = normalize_bloodline(name)
        if not isinstance(k, str):
            continue
        sub = getter(k)
        if sub is None:
            continue
        offspring = sub[sub["date"] < as_of]
        if len(offspring):
            out[key] = (offspring["finish"] <= 3).mean()
    return out
