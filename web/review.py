"""レースの回顧 (終わったレースの振り返り) と「次走へのメモ」。

- レース: ペース (ハイ/ミドル/スロー)・レースの質 (持久力戦/瞬発力戦/消耗戦)・決着 (前残り/差し)
- 各馬: 位置取り・上がり順位・流れが合ったか・敗因・次走へのメモ
  (巻き返し/見直し/信頼/割引/力負け)

レースの質はラップの形 (前半3F・中盤・上がり3F・最後の1Fの失速) を
同じ競馬場×芝ダ×距離の平均と比べ、3つの型に分ける (k-means、学習は2020〜2024年)。
次走へのメモが本当に次走の成績と関係あるかは validate() で確かめる。
"""
import sqlite3

import numpy as np
import pandas as pd

QUALITIES = ["持久力戦", "瞬発力戦", "消耗戦"]
QUALITY_TEXT = {
    "持久力戦": "道中から速いラップが続き終盤もあまり落ちない（長くいい脚が問われる）",
    "瞬発力戦": "前半〜中盤がゆったりで終盤に一気に加速（切れ味が問われる）",
    "消耗戦": "前半から速く終盤にラップが落ちて上がりがかかる（バテない馬・差しが届く）",
}
MEMOS = ["巻き返し", "見直し", "信頼", "割引", "力負け"]
MEMO_HELP = {
    "巻き返し": "出遅れ・展開不利で上がり上位、または3着と0.2秒以内",
    "見直し": "展開不利で勝ち馬と1秒以内",
    "信頼": "展開不利をはね返した好走・完勝",
    "割引": "展開に恵まれた好走で上がりは下位",
    "力負け": "勝ち馬と1.5秒以上の大敗",
}


def _laps(s):
    if not isinstance(s, str):
        return []
    try:
        return [float(x) for x in str(s).split(",") if x.strip()]
    except ValueError:
        return []


def _dev(df, col, keys=("place", "surface", "distance"), min_n=30):
    """同じ競馬場×芝ダ×距離の中央値との差。レースが少ないコースは芝ダ×距離で比べる。"""
    keys = list(keys)
    med = df.groupby(keys)[col].transform("median")
    n = df.groupby(keys)[col].transform("count")
    med2 = df.groupby(["surface", "distance"])[col].transform("median")
    return df[col] - med.where(n >= min_n, med2)


def race_shape(conn, fit_until="2025-01-01"):
    """各レースのペース・レースの質と、その根拠になる数値。"""
    r = pd.read_sql(
        "SELECT race_id, date, place, surface, distance, lap, pace_s3, pace_l3 FROM races "
        "WHERE pace_s3 IS NOT NULL AND pace_l3 IS NOT NULL "
        "AND surface IN ('芝','ダ')", conn)
    if r.empty:
        return pd.DataFrame()
    laps = r["lap"].map(_laps)
    r["n_laps"] = laps.map(len)
    has_lap = (r["n_laps"] >= 5).to_numpy()
    # 中盤 = 前半3F と 上がり3F を除いた区間の平均ラップ
    def mid(ls):
        body = ls[2:-3] if len(ls) > 5 else ls[1:-3]
        return float(np.mean(body)) if body else np.nan
    r["mid"] = laps.map(lambda ls: mid(ls) if len(ls) >= 5 else np.nan)
    # 最後の1Fがその前の2Fよりどれだけ遅いか (失速)
    r["fade"] = laps.map(lambda ls: ls[-1] - min(ls[-3:-1]) if len(ls) >= 5 else np.nan)
    r["diff"] = r["pace_s3"] - r["pace_l3"]
    for c in ("pace_s3", "pace_l3", "mid", "fade", "diff"):
        r[c + "_dev"] = _dev(r, c)
    # ペースは insight.realized_pace_table と同じ物差し (前後半差の中央値から±0.7秒)
    r["pace"] = np.where(r["diff_dev"] <= -0.7, "ハイ",
                         np.where(r["diff_dev"] >= 0.7, "スロー", "ミドル"))

    from sklearn.cluster import KMeans
    cols = ["pace_s3_dev", "mid_dev", "pace_l3_dev", "fade_dev"]
    X = r[cols].fillna(0.0)
    fit = (r["date"] < fit_until).to_numpy() & has_lap
    mu, sd = X[fit].mean(), X[fit].std().replace(0, 1)
    km = KMeans(n_clusters=3, n_init=10, random_state=0).fit(((X - mu) / sd)[fit])
    lab = km.predict((X - mu) / sd)
    cen = pd.DataFrame(km.cluster_centers_, columns=cols)
    # 名前付け: 中盤が遅く上がりが速い型=瞬発力戦、最後に失速する型=消耗戦、残り=持久力戦
    burst = int((cen["mid_dev"] - cen["pace_l3_dev"]).idxmax())
    rest = [i for i in range(3) if i != burst]
    fade = max(rest, key=lambda i: cen.loc[i, "fade_dev"] - cen.loc[i, "pace_s3_dev"])
    names = {burst: "瞬発力戦", fade: "消耗戦"}
    names.update({i: "持久力戦" for i in range(3) if i not in names})
    r["quality"] = [names[i] if ok else None for i, ok in zip(lab, has_lap)]
    return r.set_index("race_id")


def _settle(ratio_top3):
    if ratio_top3 is None or np.isnan(ratio_top3):
        return None
    if ratio_top3 <= 0.25:
        return "前残り"
    if ratio_top3 >= 0.5:
        return "差し・追込決着"
    return "平均的"


def review(conn, shape, date_from=None):
    """全出走馬の回顧。戻り値は1行=1頭 (race_id, horse_no, horse_id, date …, memo)。"""
    q = ("SELECT race_id, date, horse_id, horse_no, finish, corner1, agari, time_sec, "
         "odds, popularity FROM results WHERE is_nar = 0")
    h = pd.read_sql(q, conn)
    h["date"] = pd.to_datetime(h["date"])
    h = h[h["race_id"].isin(shape.index)].copy()
    h["n"] = h.groupby("race_id")["horse_id"].transform("size")
    h["c1r"] = (h["corner1"] - 1) / (h["n"] - 1).clip(lower=1)   # 0=先頭 1=最後方
    h = h.sort_values(["horse_id", "date", "race_id"])
    # その馬のいつもの位置 (前5走の平均)
    h["usual"] = (h.groupby("horse_id")["c1r"]
                  .transform(lambda s: s.shift().rolling(5, min_periods=2).mean()))

    ok = h["finish"].notna() & (h["finish"] > 0)
    g = h[ok].groupby("race_id")
    h["agari_rank"] = g["agari"].rank(method="min").reindex(h.index)
    win_t = h[ok & (h["finish"] == 1)].groupby("race_id")["time_sec"].min()
    third_t = h[ok & (h["finish"] <= 3)].groupby("race_id")["time_sec"].max()
    second_t = h[ok & (h["finish"] == 2)].groupby("race_id")["time_sec"].min()
    h["win_by"] = (h["race_id"].map(second_t) - h["time_sec"]).round(1).where(h["finish"] == 1)
    h["to_win"] = (h["time_sec"] - h["race_id"].map(win_t)).round(1)
    h["to_3rd"] = (h["time_sec"] - h["race_id"].map(third_t)).round(1)
    top3_c1r = h[ok & (h["finish"] <= 3)].groupby("race_id")["c1r"].mean()
    h["settle"] = h["race_id"].map(top3_c1r).map(_settle)
    h["pace"] = h["race_id"].map(shape["pace"])
    h["quality"] = h["race_id"].map(shape["quality"])
    if date_from is not None:
        h = h[h["date"] >= pd.Timestamp(date_from)]

    front = h["c1r"] <= 0.3
    back = h["c1r"] >= 0.6
    # 流れ: 決着 (上位3頭の位置) とペースに対して、その馬の位置取りが有利だったか
    bad = ((h["settle"] == "前残り") & back) | ((h["settle"] == "差し・追込決着") & front) \
        | ((h["pace"] == "ハイ") & front & (h["settle"] != "前残り")) \
        | ((h["pace"] == "スロー") & back & (h["settle"] != "差し・追込決着"))
    good = ((h["settle"] == "前残り") & front) | ((h["settle"] == "差し・追込決着") & back)
    h["flow"] = np.where(bad, "不利", np.where(good, "有利", "ふつう"))
    late = (h["c1r"] - h["usual"]) >= 0.3   # いつもより大きく後ろ (出遅れ・位置取り)
    n = h["n"]
    ar = h["agari_rank"]
    lost = h["finish"] >= 4

    cause = np.select(
        [lost & (h["to_3rd"] <= 0.1),
         lost & late,
         lost & (h["flow"] == "不利") & back & (ar <= 3),
         lost & (h["flow"] == "不利") & back,
         lost & front & (ar > n / 2)],
        ["惜敗", "出遅れ・位置取り", "前残りで届かず", "前残りで届かず", "先行して失速"],
        default=None)
    h["cause"] = np.where(lost & pd.isna(cause), "伸びず", cause)
    h.loc[~ok, "cause"] = None

    rebound = lost & (h["to_3rd"] <= 0.2) | lost & ((h["flow"] == "不利") | late) & (ar <= 3)
    review_ = lost & (h["flow"] == "不利") & (h["to_win"] <= 1.0)
    trust = (h["finish"] <= 3) & ((h["flow"] == "不利")
                                   | (h["win_by"] >= 0.3))
    discount = (h["finish"] <= 3) & (h["flow"] == "有利") & (ar > n / 2)
    beaten = lost & (h["to_win"] >= 1.5)
    h["memo"] = np.select([rebound, review_, trust, discount, beaten], MEMOS, default=None)
    h.loc[~ok, "memo"] = None
    return h


def validate(h, since="2021-01-01"):
    """メモごとに、次走の3着内率を「同じ人気の馬の平均」と比べる。"""
    d = h.sort_values(["horse_id", "date", "race_id"]).copy()
    nxt = d.groupby("horse_id")
    d["next_top3"] = (nxt["finish"].shift(-1) <= 3).astype(float) \
        .where(nxt["finish"].shift(-1).notna())
    d["next_pop"] = nxt["popularity"].shift(-1)
    d["next_gap"] = (nxt["date"].shift(-1) - d["date"]).dt.days
    d = d[(d["date"] >= since) & d["next_top3"].notna() & d["next_pop"].notna()
          & (d["next_gap"] <= 120)]
    base = d.groupby(d["next_pop"].clip(upper=12))["next_top3"].mean()
    d["exp"] = d["next_pop"].clip(upper=12).map(base)
    out = {}
    for m in MEMOS + [None]:
        s = d[d["memo"].isna()] if m is None else d[d["memo"] == m]
        if len(s) < 50:
            continue
        out[m or "メモなし"] = {
            "n": int(len(s)),
            "next_top3": round(float(s["next_top3"].mean()), 4),
            "expected": round(float(s["exp"].mean()), 4),
            "lift": round(float(s["next_top3"].mean() - s["exp"].mean()), 4),
        }
    return out


def load(db_path, date_from=None):
    with sqlite3.connect(db_path) as conn:
        shape = race_shape(conn)
        h = review(conn, shape, date_from)
    return shape, h


# ---------- サイト用 ----------

def _s(v):
    return None if v is None or (not isinstance(v, str) and pd.isna(v)) else v


def _f1(v):
    return None if v is None or pd.isna(v) else round(float(v), 1)


def _race_text(rs, hr):
    """レースの回顧の文 (ペース・ラップ・展開)。rs: race_shape の1行, hr: そのレースの馬の回顧"""
    out = {}
    if rs is not None:
        s3, l3 = rs["pace_s3_dev"], rs["pace_l3_dev"]
        def cmp(v, fast, slow):
            if pd.isna(v) or abs(v) < 0.3:
                return "平均並み"
            return f"{abs(v):.1f}秒{fast if v < 0 else slow}"
        out["pace"] = (f"ペースは{rs['pace']}（前半3F {rs['pace_s3']:.1f}・上がり3F {rs['pace_l3']:.1f}）。"
                       f"前半はコース平均より{cmp(s3, '速い', '遅い')}、上がりは{cmp(l3, '速い', 'かかった')}。")
        if _s(rs["quality"]):
            out["quality"] = f"{rs['quality']}：{QUALITY_TEXT[rs['quality']]}。"
    win = hr[hr["finish"] == 1]
    top3 = hr[hr["finish"] <= 3]
    if len(top3) and top3["corner1"].notna().all():
        settle = top3["settle"].iloc[0]
        txt = f"上位3頭は序盤平均{top3['corner1'].mean():.1f}番手"
        txt += {"前残り": "で前残りの決着。", "差し・追込決着": "で差し・追込の決着。"}.get(settle, "で平均的な決着。")
        if len(win) and pd.notna(win["corner1"].iloc[0]):
            w = win.iloc[0]
            txt += f"勝ち馬は序盤{int(w['corner1'])}番手から上がり{int(w['agari_rank'])}位。" \
                if pd.notna(w["agari_rank"]) else ""
        fast = hr[hr["agari_rank"] == 1]
        if len(fast) and pd.notna(fast["finish"].iloc[0]):
            txt += f"上がり最速の馬は{int(fast['finish'].iloc[0])}着。"
        out["settle"] = txt
    return out


def site_payload(db_path, race_ids, race_dates, entry_horses):
    """書き出し用の回顧。

    race_ids: サイトに載せるレース, race_dates: race_id→日付
    entry_horses: (race_id, horse_no)→horse_id (前走メモを引くため。これからのレースも含む)
    戻り値: (races{race_id: {...}}, entries{(race_id, horse_no): {...}}, 検証)
    """
    shape, h = load(db_path)
    want = set(race_ids)
    races = {}
    hs = h[h["race_id"].isin(want)]
    for rid, hr in hs.groupby("race_id"):
        rs = shape.loc[rid] if rid in shape.index else None
        info = {"text": _race_text(rs, hr)}
        if rs is not None:
            info.update(pace=rs["pace"], quality=_s(rs["quality"]),
                        laps=_laps(rs["lap"]), s3=_f1(rs["pace_s3"]), l3=_f1(rs["pace_l3"]))
        info["settle"] = hr["settle"].dropna().iloc[0] if hr["settle"].notna().any() else None
        races[rid] = info
    # ラップはあるが馬の成績が無いレース (取消だけ等) は飛ばす。ラップだけ載せる
    for rid in want - set(races):
        if rid in shape.index:
            rs = shape.loc[rid]
            races[rid] = {"pace": rs["pace"], "quality": _s(rs["quality"]), "laps": _laps(rs["lap"]),
                          "s3": _f1(rs["pace_s3"]), "l3": _f1(rs["pace_l3"]),
                          "text": _race_text(rs, h.iloc[0:0])}

    entries = {}
    for r in hs.itertuples(index=False):
        if pd.isna(r.finish):
            continue
        entries[(r.race_id, int(r.horse_no))] = {
            "pos": None if pd.isna(r.corner1) else int(r.corner1),
            "ar": None if pd.isna(r.agari_rank) else int(r.agari_rank),
            "tw": _f1(r.to_win),
            "flow": _s(r.flow), "cause": _s(r.cause), "memo": _s(r.memo),
        }

    # 前走メモ: そのレースより前の、その馬の一番新しい回顧
    if entry_horses:
        q = pd.DataFrame([(rid, no, hid, pd.Timestamp(race_dates.get(rid)))
                          for (rid, no), hid in entry_horses.items() if hid],
                         columns=["race_id", "horse_no", "horse_id", "date"]).dropna()
        if not q.empty:
            past = h[["horse_id", "date", "race_id", "memo", "cause", "flow", "finish"]] \
                .rename(columns={"race_id": "p_race", "date": "p_date"}).dropna(subset=["finish"])
            past = past.sort_values("p_date")
            q = q.sort_values("date")
            m = pd.merge_asof(q, past, left_on="date", right_on="p_date", by="horse_id",
                              allow_exact_matches=False)
            m = m[m["p_race"].notna() & ((m["date"] - m["p_date"]).dt.days <= 180)]
            for r in m.itertuples(index=False):
                e = entries.setdefault((r.race_id, int(r.horse_no)), {})
                e["prev"] = {"memo": _s(r.memo), "cause": _s(r.cause), "flow": _s(r.flow),
                             "finish": int(r.finish), "date": r.p_date.strftime("%Y-%m-%d")}
    return races, entries, validate(h)
