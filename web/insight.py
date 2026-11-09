"""推奨レースの「指数」と「根拠」を作るための分析。

web.build (終わったレース) と web.upcoming (これからのレース) の両方から使う。
どれも「そのレースより前に分かっていた情報」だけで計算する。

- 能力指数: オッズを使わない能力モデルの複勝率を、全出走馬の中の位置 (0-100) に直したもの
- 展開: 出走馬の近走の位置取りから想定ペースを出し、本命の脚質と噛み合うかを見る
- 騎手×調教師: そのコンビの過去の複勝率
- 人気帯: 「1番人気でない堅軸」は実際どれくらい来ているか
"""
import json
import sqlite3

import numpy as np
import pandas as pd

# ---------- 能力指数 ----------

INDEX_QUANTILES = np.linspace(0, 1, 101)


def index_table(ability_p):
    """能力モデルの確率 → 指数 の対応表 (分位点) を作る。"""
    v = pd.Series(ability_p).dropna().astype(float)
    return np.quantile(v, INDEX_QUANTILES).tolist() if len(v) else []


def to_index(ability_p, table):
    """確率を 0-100 の指数にする (全出走馬の上位何%か)。"""
    p = np.asarray(ability_p, dtype=float)
    if not table:
        return np.full(len(p), np.nan)
    t = np.asarray(table)
    right = np.searchsorted(t, p, side="right")
    left = np.searchsorted(t, p, side="left")
    # 同じ値が何分位にもまたがる (同点が多い) ときは、その真ん中の順位にする
    idx = np.where(left == right, right - 1, (left + right - 1) / 2.0)
    return np.where(np.isnan(p), np.nan, np.clip(np.round(idx), 0, 100))


def save_meta(conn, key, value):
    conn.execute("CREATE TABLE IF NOT EXISTS site_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO site_meta VALUES (?, ?)",
                 (key, json.dumps(value, ensure_ascii=False)))


def load_meta(db_path, key, default=None):
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT value FROM site_meta WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default
    return json.loads(row[0]) if row else default


# ---------- 展開 (ペース) ----------

# 近5走の「1コーナー通過順 ÷ 頭数」の平均で脚質を分ける
STYLE_BINS = [(0.15, "逃げ"), (0.40, "先行"), (0.70, "差し"), (1.01, "追込")]


def style_of(ratio):
    if ratio is None or (isinstance(ratio, float) and np.isnan(ratio)):
        return None
    for th, name in STYLE_BINS:
        if ratio <= th:
            return name
    return "追込"


def realized_pace_table(db_path):
    """終わったレースの実際のペース (スロー/ミドル/ハイ) を race_id ごとに出す。

    前半3Fと後半3Fの差を、同じ芝ダ・距離の中央値と比べる。
    全体の約3割ずつがハイ・スローになる境目 (±0.7秒) で切る。
    """
    with sqlite3.connect(db_path) as conn:
        r = pd.read_sql(
            "SELECT race_id, surface, distance, pace_s3, pace_l3 FROM races "
            "WHERE pace_s3 IS NOT NULL AND pace_l3 IS NOT NULL", conn)
    if r.empty:
        return {}
    r["diff"] = r["pace_s3"] - r["pace_l3"]
    r["dev"] = r["diff"] - r.groupby(["surface", "distance"])["diff"].transform("median")
    cat = np.where(r["dev"] <= -0.7, "ハイ", np.where(r["dev"] >= 0.7, "スロー", "ミドル"))
    return dict(zip(r["race_id"], cat))


def predict_pace(ratios):
    """出走馬の脚質から想定ペースを出す。返り値: (ペース, 先行馬の頭数)

    近5走で前 (先行度0.25以下) にいる馬の頭数が一番よく効く
    (8,395レースで検証: 0頭なら前後半差が平均+0.37秒でスロー寄り、
    4頭以上なら-0.2〜-0.5秒でハイ寄り)。差がはっきりする両端だけ
    スロー/ハイと言い、それ以外はミドルとする。
    「逃げ馬が1頭ならスロー」はデータでは支持されなかったので使わない。
    """
    r = pd.Series(ratios, dtype=float).dropna()
    if r.empty:
        return None, 0
    n_front = int((r <= 0.25).sum())
    if n_front == 0:
        pace = "スロー"
    elif n_front >= 4:
        pace = "ハイ"
    else:
        pace = "ミドル"
    return pace, n_front


# 想定ペースと本命の脚質が噛み合うか
_GOOD_FIT = {("スロー", "逃げ"), ("スロー", "先行"), ("ハイ", "差し"), ("ハイ", "追込")}
_BAD_FIT = {("スロー", "追込"), ("ハイ", "逃げ")}


def pace_text(pace, n_front, style, same_pace_good):
    """展開の一文。ペース予想の当たりは控えめなので「寄り」と書く。

    検証 (24,074レース): 想定スロー→実際スロー35% (全体29%)、
    想定ハイ→実際ハイ34% (全体31%)・実際スロー20%。ミドルは全体と差が無く
    情報にならないので書かない。
    """
    if pace == "スロー":
        head = "先行馬不在でスロー寄り"
    elif pace == "ハイ":
        head = f"先行馬{n_front}頭でハイ寄り"
    else:
        return None
    parts = [head]
    if style:
        if (pace, style) in _GOOD_FIT:
            parts.append(f"{style}の本命に向く流れ")
        elif (pace, style) in _BAD_FIT:
            parts.append(f"{style}の本命には不利な流れ")
    if same_pace_good:
        parts.append("同ペースで好走実績")
    return "展開：" + "・".join(parts)


def same_pace_good(horse_hist, pace, pace_map):
    """その馬が、想定と同じペースのレースで3着以内に来たことがあるか。"""
    if horse_hist is None or len(horse_hist) == 0 or pace is None:
        return False
    for rid, fin in zip(horse_hist["race_id"], horse_hist["finish"]):
        if pace_map.get(rid) == pace and pd.notna(fin) and fin <= 3:
            return True
    return False


# ---------- 騎手×調教師 ----------

def jt_rates(df):
    """各出走行に、そのレースより前の騎手×調教師コンビの (出走数, 複勝率) を付ける。"""
    d = df[["jockey_id", "trainer_id", "date", "race_id", "finish"]].copy()
    d["_top3"] = (d["finish"] <= 3).astype(float)
    d = d.sort_values(["date", "race_id"])
    ok = d["jockey_id"].notna() & d["trainer_id"].notna()
    g = d[ok].groupby(["jockey_id", "trainer_id"], sort=False)
    n = g.cumcount()
    s = g["_top3"].cumsum() - d.loc[ok, "_top3"]
    out_n = pd.Series(np.nan, index=d.index)
    out_r = pd.Series(np.nan, index=d.index)
    out_n.loc[ok] = n
    out_r.loc[ok] = s / n.replace(0, np.nan)
    return out_n.reindex(df.index), out_r.reindex(df.index)


def jt_rate_asof(hist_df, jockey_id, trainer_id, as_of):
    if hist_df is None or not jockey_id or not trainer_id:
        return 0, np.nan
    m = ((hist_df["jockey_id"] == jockey_id) & (hist_df["trainer_id"] == trainer_id)
         & (hist_df["date"] < as_of))
    sub = hist_df.loc[m, "finish"]
    return len(sub), ((sub <= 3).mean() if len(sub) else np.nan)


JT_MIN_RIDES = 10
JT_MIN_RATE = 0.40


def jt_text(rate, n, rank_in_race):
    if n is None or np.isnan(rate) or n < JT_MIN_RIDES or rate < JT_MIN_RATE:
        return None
    where = "・レース内1位" if rank_in_race == 1 else ""
    return f"騎手好調（騎手×調教師コンビの複勝率{rate * 100:.0f}%／{int(n)}戦{where}）"


# ---------- 人気帯 ----------

def popularity_stats(rec_rows):
    """推奨(堅軸)の本命について、人気帯ごとの複勝率を集計する。"""
    done = rec_rows[rec_rows["finish"].notna() & rec_rows["popularity"].notna()]
    out = {}
    for key, mask in (("1", done["popularity"] == 1),
                      ("2-3", done["popularity"].between(2, 3)),
                      ("4+", done["popularity"] >= 4)):
        sub = done[mask]
        if len(sub) >= 30:
            out[key] = {"n": int(len(sub)), "top3": float((sub["finish"] <= 3).mean())}
    return out


def popularity_text(pop, stats):
    if pop is None or pd.isna(pop):
        return None
    pop = int(pop)
    s1, s23, s4 = stats.get("1"), stats.get("2-3"), stats.get("4+")
    if pop == 1:
        return (f"1番人気（堅軸の1番人気は過去 複勝率{s1['top3'] * 100:.0f}%）"
                if s1 else "1番人気")
    if pop <= 3:
        if s1 and s23:
            word = "やや低め" if s23["top3"] < s1["top3"] else "同等"
            return (f"{pop}番人気（1番人気でない堅軸は複勝率が{word}：過去"
                    f" {s23['top3'] * 100:.0f}%、1番人気なら{s1['top3'] * 100:.0f}%）")
        return f"{pop}番人気"
    if s4:
        return f"{pop}番人気の穴（同条件の過去 複勝率{s4['top3'] * 100:.0f}%）"
    return f"{pop}番人気の穴"


# ---------- 払戻の注記 ----------

def pay_note(finish, fuku_pay, n_same_finish):
    """複勝払戻に添える注記。普段は空。"""
    notes = []
    if finish is not None and not pd.isna(finish) and n_same_finish and n_same_finish > 1:
        notes.append("同着")
    if fuku_pay == 100:
        notes.append("元返し")
    if finish is not None and not pd.isna(finish) and finish <= 3 and fuku_pay is None:
        notes.append("払戻データなし")
    return "・".join(notes) or None


# ---------- 能力指数の内訳 (レースページの「能力指数」タブ) ----------
#
# どれも「そのレースより前」の成績だけから作り、全出走馬の中での位置 (0-100) にする。
# 50 が全馬の真ん中。データの無い項目は 50 として表示する。

# (列名, 表示名, 向き): 向き -1 は「小さいほど良い」
# 定義は過去レースで候補を比べて選んだもの (scripts/refine_components.py の結果、
# 選定 2025/1〜2026/5/18・確認 2026/5/19〜 のレース内AUC)。
COMPONENTS = [
    ("c_spd", "走破", 1),      # 走破タイム (競馬場・芝ダ・距離・馬場で標準化) を近走ほど重く平均
    ("c_agari", "上がり", -1),  # 上がり3Fのレース内順位を近走ほど重く平均
    ("c_recent", "近走", -1),   # 着順の位置 (着順÷頭数) を近走ほど重く平均
    ("c_career", "着順", -1),   # 通算の着順の位置の平均 (複勝率より良く効いた)
    ("c_dist", "距離", 1),      # 同じ距離帯での複勝率
    ("c_course", "コース", 1),  # 同じ競馬場での複勝率
    ("c_jockey", "騎手", 1),    # 騎手の複勝率
    ("c_trainer", "厩舎", 1),   # 調教師の複勝率
    ("c_sire", "血統", 1),      # 父の産駒の、同じ芝ダでの複勝率
    ("c_going", "馬場", 1),     # 同じ馬場 (良 / 道悪) での複勝率
]
# 特徴量 (X) にもともとある項目
# 特徴量 (X) からそのまま使う項目 (今は無し: 血統も芝ダ別に作り直した)
COMPONENT_FROM_X = {}
EWM_SPAN = 3          # 「近走ほど重く」の重み (指数平滑の span)
SHRINK_SIRE = 30

# 複勝率は少ない出走数だと 1/1=100% のように極端になるので、
# 全体の平均に向けて縮める (k 走ぶんの「平均的な成績」を足してから割る)
SHRINK_HORSE = 3     # 馬ごとの項目 (通算・距離・コース・馬場)
SHRINK_PERSON = 30   # 騎手・調教師
PERSON_MIN_STARTS = 10


def _shrunk(top3, n, k, m):
    return (top3 + k * m) / (n + k)


def going_group(going):
    if going is None or (isinstance(going, float) and np.isnan(going)):
        return None
    return "良" if going == "良" else "道悪"


def run_figures(df):
    """1走ごとの数値 (走破タイムの偏差・上がりの偏差・着順の位置など) を足す。"""
    d = df.copy()
    d["going_grp"] = d["going"].map(going_group)
    d["place_cd"] = d["race_id"].str[4:6]
    d["dist_band"] = d["distance"] // 400
    key = [d["surface"], d["distance"], d["going_grp"]]
    t = d["time_sec"].where(d["time_sec"] > 0)
    mean = t.groupby(key).transform("mean")
    std = t.groupby(key).transform("std").replace(0, np.nan)
    d["spd_z"] = (mean - t) / std                       # 大きいほど速い
    keyB = [d["place_cd"], d["surface"], d["distance"], d["going_grp"]]
    d["spd_zB"] = ((t.groupby(keyB).transform("mean") - t)
                   / t.groupby(keyB).transform("std").replace(0, np.nan))
    d["agari_dev"] = d["agari"] - d.groupby("race_id")["agari"].transform("mean")
    fs = d.groupby("race_id")["horse_no"].transform("count")
    d["fin_norm"] = d["finish"] / fs
    d["agari_rank"] = d.groupby("race_id")["agari"].rank(method="average") / fs
    d["_top3"] = (d["finish"] <= 3).astype(float)
    return d


def _cum_rate(d, keys, k, m, min_n=1):
    """keys ごとに、その行より前の複勝率 (縮めたもの)。母数が min_n 未満なら NaN。"""
    kk = d[keys].astype(object).where(d[keys].notna(), "_na")
    g = d.groupby([kk[c] for c in keys], sort=False)
    n = g.cumcount()
    s = g["_top3"].cumsum() - d["_top3"]
    rate = _shrunk(s, n, k, m)
    has_key = d[keys].notna().all(axis=1)
    return rate.where((n >= min_n) & has_key)


def prior_components(df):
    """各出走行に、そのレースより前の成績から作る内訳 (生の値) を付ける。"""
    d = run_figures(df).sort_values(["date", "race_id"])
    m = float(d["_top3"].mean())
    g = d.groupby("horse_id", sort=False)
    out = pd.DataFrame(index=d.index)
    def ewm_prior(col):
        s = g[col].shift()
        return s.groupby(d["horse_id"], sort=False).transform(
            lambda x: x.ewm(span=EWM_SPAN, ignore_na=True).mean())

    out["c_spd"] = ewm_prior("spd_zB")
    out["c_agari"] = ewm_prior("agari_rank")
    out["c_recent"] = ewm_prior("fin_norm")
    fn = d["fin_norm"]
    cnt = fn.notna().astype(float)
    tot = fn.fillna(0)
    n_prior = cnt.groupby(d["horse_id"]).cumsum() - cnt
    s_prior = tot.groupby(d["horse_id"]).cumsum() - tot
    out["c_career"] = (s_prior / n_prior.replace(0, np.nan))
    if "sire_key" in d:
        out["c_sire"] = _cum_rate(d, ["sire_key", "surface"], SHRINK_SIRE, m)
    out["c_dist"] = _cum_rate(d, ["horse_id", "dist_band"], SHRINK_HORSE, m)
    out["c_course"] = _cum_rate(d, ["horse_id", "place_cd"], SHRINK_HORSE, m)
    out["c_going"] = _cum_rate(d, ["horse_id", "going_grp"], SHRINK_HORSE, m)
    out["c_jockey"] = _cum_rate(d, ["jockey_id"], SHRINK_PERSON, m, PERSON_MIN_STARTS)
    out["c_trainer"] = _cum_rate(d, ["trainer_id"], SHRINK_PERSON, m, PERSON_MIN_STARTS)
    return out.reindex(df.index)


def figures_index(hist_df):
    """予想時用に、run_figures 済みの過去成績を馬・騎手・調教師ごとに引けるようにする。"""
    d = run_figures(hist_df).sort_values("date")
    cols = ["date", "spd_zB", "agari_rank", "fin_norm", "finish", "going_grp",
            "place_cd", "dist_band"]
    small = ["date", "finish"]
    return {
        "mean_top3": float(d["_top3"].mean()),
        "by_horse": {k: v[cols] for k, v in d.groupby("horse_id", sort=False)},
        "by_trainer": {k: v[small] for k, v in
                       d[d["trainer_id"].notna()].groupby("trainer_id", sort=False)},
        "by_jockey": {k: v[small] for k, v in
                      d[d["jockey_id"].notna()].groupby("jockey_id", sort=False)},
        "by_sire_surf": ({k: v[small] for k, v in d[d["sire_key"].notna()].groupby(
                          ["sire_key", "surface"], sort=False)} if "sire_key" in d else {}),
    }


def components_asof(figs, horse_id, trainer_id, going, as_of, jockey_id=None,
                    place_cd=None, distance=None, surface=None, sire_key=None):
    """予想時に使う: 1頭ぶんの内訳 (生の値)。prior_components と同じ定義。"""
    out = {c: np.nan for c in ("c_spd", "c_agari", "c_recent", "c_career", "c_dist",
                               "c_course", "c_going", "c_jockey", "c_trainer", "c_sire")}
    m = figs.get("mean_top3", 0.21)

    def rate(rows, k, min_n=1):
        n = len(rows)
        return _shrunk((rows["finish"] <= 3).sum(), n, k, m) if n >= min_n else np.nan

    h = figs.get("by_horse", {}).get(horse_id)
    if h is not None:
        h = h[h["date"] < as_of]
        if len(h):
            def ewm_last(col):
                v = h[col].ewm(span=EWM_SPAN, ignore_na=True).mean()
                return v.iloc[-1] if len(v) else np.nan

            out["c_spd"] = ewm_last("spd_zB")
            out["c_agari"] = ewm_last("agari_rank")
            out["c_recent"] = ewm_last("fin_norm")
            out["c_career"] = h["fin_norm"].mean()
            if distance:
                out["c_dist"] = rate(h[h["dist_band"] == int(distance) // 400], SHRINK_HORSE)
            if place_cd:
                out["c_course"] = rate(h[h["place_cd"] == place_cd], SHRINK_HORSE)
            out["c_going"] = rate(h[h["going_grp"] == (going_group(going) or "良")],
                                  SHRINK_HORSE)
    if sire_key and surface:
        rows = figs.get("by_sire_surf", {}).get((sire_key, surface))
        if rows is not None:
            out["c_sire"] = rate(rows[rows["date"] < as_of], SHRINK_SIRE)
    for key, pid, src in (("c_trainer", trainer_id, "by_trainer"),
                          ("c_jockey", jockey_id, "by_jockey")):
        rows = figs.get(src, {}).get(pid) if pid else None
        if rows is not None:
            out[key] = rate(rows[rows["date"] < as_of], SHRINK_PERSON, PERSON_MIN_STARTS)
    return out


def component_tables(frame):
    """各項目の分位点 (0-100) を作る。向きを揃えて「大きいほど良い」にしてから。"""
    tables = {}
    for col, _, sign in COMPONENTS:
        if col in frame:
            tables[col] = index_table(sign * pd.to_numeric(frame[col], errors="coerce"))
    return tables


def component_scores(frame, tables):
    """生の値 → 0-100 の点数 (データが無ければ NaN。表示では50にする)。"""
    out = pd.DataFrame(index=frame.index)
    for col, _, sign in COMPONENTS:
        v = pd.to_numeric(frame.get(col), errors="coerce") if col in frame else np.nan
        out[col] = to_index(sign * np.asarray(v, dtype=float), tables.get(col, []))
    return out


# ---------- 指数 (総合) ----------
#
# AI能力モデル (オッズなし) だけより、内訳10項目の合成を3割混ぜた方が
# 当たり方が良かった (確認期間のレース内AUC 0.731 → 0.740、指数1位の複勝率 56.4% → 57.7%)。
INDEX_W_COMP = 0.3


def _comp_matrix(scores):
    return np.column_stack([
        pd.to_numeric(scores[c], errors="coerce").fillna(50).to_numpy() / 100.0
        for c, _, _ in COMPONENTS])


def fit_index_blend(ai_idx, scores, y, fit_mask):
    """内訳の合成の重みを学習期間で決め、指数の物差しを作る。"""
    from sklearn.linear_model import LogisticRegression

    Xc = _comp_matrix(scores)
    m = np.asarray(fit_mask) & ~np.isnan(np.asarray(y, dtype=float))
    lr = LogisticRegression(max_iter=1000).fit(Xc[m], np.asarray(y)[m])
    p = {"coef": [float(v) for v in lr.coef_[0]], "intercept": float(lr.intercept_[0]),
         "w": INDEX_W_COMP}
    comp_raw = Xc @ np.asarray(p["coef"]) + p["intercept"]
    p["comp_table"] = index_table(comp_raw)
    blend = _blend_raw(ai_idx, comp_raw, p)
    p["blend_table"] = index_table(blend)
    return p


def _blend_raw(ai_idx, comp_raw, p):
    ai = np.asarray(pd.to_numeric(pd.Series(ai_idx), errors="coerce").fillna(50), dtype=float) / 100
    comp = to_index(comp_raw, p["comp_table"]) / 100
    return (1 - p["w"]) * ai + p["w"] * np.nan_to_num(comp, nan=0.5)


def apply_index_blend(ai_idx, scores, p):
    """AIの指数と内訳から総合の指数 (0-100) を出す。物差しが無ければAIの指数のまま。"""
    if not p or "coef" not in p:
        return np.asarray(ai_idx, dtype=float)
    comp_raw = _comp_matrix(scores) @ np.asarray(p["coef"]) + p["intercept"]
    return to_index(_blend_raw(ai_idx, comp_raw, p), p["blend_table"])


def index_report(idx, scores, race_id, finish, mask):
    """検証期間での指数帯ごとの実績と、各項目の当たりやすさ (レース内AUC)。"""
    df = pd.DataFrame({"race": race_id, "idx": idx, "fin": finish})[mask].dropna(subset=["fin"])
    df["top3"] = (df["fin"] <= 3).astype(float)
    df["win"] = (df["fin"] == 1).astype(float)
    bins = [-1, 50, 70, 80, 90, 95, 100]
    labels = ["0-50", "51-70", "71-80", "81-90", "91-95", "96-100"]
    df["band"] = pd.cut(df["idx"], bins, labels=labels)
    bands = [{"band": str(b), "n": int(len(g)), "top3": round(float(g["top3"].mean()), 3),
              "win": round(float(g["win"].mean()), 3)}
             for b, g in df.groupby("band", observed=True) if len(g)]

    def auc(s):
        x = pd.DataFrame({"race": df["race"], "s": s.reindex(df.index).fillna(50), "y": df["top3"]})
        x["r"] = x.groupby("race")["s"].rank(method="average")
        g = x.groupby("race")
        npos, n = g["y"].sum(), g["y"].count()
        nneg = n - npos
        spos = (x["r"] * x["y"]).groupby(x["race"]).sum()
        ok = (npos > 0) & (nneg > 0)
        return round(float(((spos - npos * (npos + 1) / 2) / (npos * nneg))[ok].mean()), 3)

    aucs = {"指数": auc(pd.Series(idx, index=race_id.index))}
    for c, name, _ in COMPONENTS:
        aucs[name] = auc(scores[c])
    top = df.sort_values("idx", ascending=False).groupby("race").head(1)
    return {"from": None, "bands": bands, "auc": aucs, "n_races": int(df["race"].nunique()),
            "top1_top3": round(float(top["top3"].mean()), 3),
            "top1_win": round(float(top["win"].mean()), 3)}
