"""3連単100万円超え (以下「100万馬券」) が出やすいレースの法則。

レースの条件 (頭数・クラス・距離・馬場…) とオッズの形を「出走前に分かる値」だけで表にし、
過去レースで 100万馬券が出た割合の高い条件の組み合わせを探す (scripts/million_rules.py)。
見つけた法則は site_meta['million_rules'] に保存し、予想サイトで当てはまるレースを示す。
"""
import re

import numpy as np
import pandas as pd

MILLION = 1_000_000
LOCAL = {"札幌", "函館", "福島", "新潟", "小倉"}
GOING_BAD = {"稍重", "重", "不良"}
CLASS_WORDS = [("新馬", "新馬"), ("未勝利", "未勝利"), ("1勝", "1勝"), ("500万", "1勝"),
               ("2勝", "2勝"), ("1000万", "2勝"), ("3勝", "3勝"), ("1600万", "3勝")]
OPEN_RE = re.compile(r"\((G|GI|GII|GIII|J\.G\w*|OP|L)\)|オープン|OP$")


def prior_wins(df):
    """各出走時点での、その馬のそれまでの勝利数 (df は date 順に並べ替えて使う)。"""
    d = df.sort_values(["date", "race_id"])
    w = (d["finish"] == 1).astype(int)
    return (w.groupby(d["horse_id"]).cumsum() - w).reindex(df.index)


def _class_of(name, med_wins):
    name = name or ""
    if "障害" in name:
        return "障害"
    for word, c in CLASS_WORDS:
        if word in name:
            return c
    if OPEN_RE.search(name):
        return "OP以上"
    # 特別戦は名前にクラスが無いので、出走馬の勝ち数の中央値から推定する
    if med_wins is None or np.isnan(med_wins):
        return "不明"
    if med_wins >= 3.5:
        return "OP以上"
    if med_wins >= 2.5:
        return "3勝"
    if med_wins >= 1.5:
        return "2勝"
    return "1勝"


def race_table(h):
    """出走馬の表 (1行=1頭) からレースの表 (1行=1レース) を作る。

    必要な列: race_id, date, place, race_no, race_name, surface, distance, going,
             age, sex, kinryo, odds, prior_wins
    odds が空 (出走前) なら、オッズを使う値は NaN になる。
    """
    h = h.copy()
    h["odds"] = pd.to_numeric(h["odds"], errors="coerce").where(lambda s: s > 0)
    g = h.groupby("race_id", sort=False)
    first = g[["date", "place", "race_no", "race_name", "surface", "distance", "going"]].first()
    r = first.copy()
    r["n"] = g.size()
    r["age_min"] = g["age"].min()
    r["age_max"] = g["age"].max()
    r["mare_only"] = g["sex"].agg(lambda s: bool(len(s)) and (s == "牝").all())
    kin = pd.to_numeric(h["kinryo"], errors="coerce")
    r["kin_range"] = kin.groupby(h["race_id"]).agg(lambda s: s.max() - s.min())
    r["kin_top"] = kin.groupby(h["race_id"]).max()
    r["med_wins"] = g["prior_wins"].median()
    r["cls"] = [_class_of(n, w) for n, w in zip(r["race_name"], r["med_wins"])]
    if "grade" in h:
        # JRA-VAN 由来のレース名には (G1) や (L) が付かないので格付けの列でも見る
        gr = g["grade"].first().reindex(r.index)
        r.loc[gr.isin(["G1", "G2", "G3", "L", "重賞"]).to_numpy(), "cls"] = "OP以上"

    # オッズの形 (人気順に並べた単勝オッズ)
    o = h.dropna(subset=["odds"]).sort_values(["race_id", "odds"])
    og = o.groupby("race_id")
    r["fav"] = og["odds"].min()
    r["fav2"] = og["odds"].apply(lambda s: s.iloc[1] if len(s) > 1 else np.nan)
    r["fav3"] = og["odds"].apply(lambda s: s.iloc[2] if len(s) > 2 else np.nan)
    r["n_lt10"] = og["odds"].apply(lambda s: int((s < 10).sum()))
    r["n_lt20"] = og["odds"].apply(lambda s: int((s < 20).sum()))
    inv = 1 / o["odds"]
    p = inv / inv.groupby(o["race_id"]).transform("sum")
    r["top3_share"] = p.groupby(o["race_id"]).apply(lambda s: s.nlargest(3).sum())
    ent = -(p * np.log(p)).groupby(o["race_id"]).sum()
    r["entropy"] = ent / np.log(r["n"].reindex(ent.index).clip(lower=2))
    has = og.size().reindex(r.index).fillna(0) >= r["n"] * 0.8
    for c in ("fav", "fav2", "fav3", "n_lt10", "n_lt20", "top3_share", "entropy"):
        r[c] = r[c].where(has)
    r["month"] = pd.to_datetime(r["date"]).dt.month
    return r


def _between(col, lo=None, hi=None):
    def f(r):
        s = r[col]
        m = s.notna()
        if lo is not None:
            m &= s >= lo
        if hi is not None:
            m &= s <= hi
        return m
    return f


def _eq(col, v):
    return lambda r: r[col] == v


def _isin(col, vs):
    return lambda r: r[col].isin(vs)


# (キー, 表示, 判定, オッズを使うか)
CONDITIONS = [
    ("n16", "16頭以上", _between("n", 16), False),
    ("n14", "14頭以上", _between("n", 14), False),
    ("n10", "10頭以下", _between("n", None, 10), False),
    ("turf", "芝", _eq("surface", "芝"), False),
    ("dirt", "ダート", _eq("surface", "ダ"), False),
    ("jump", "障害", _eq("surface", "障"), False),
    ("d_s", "1400m以下", _between("distance", None, 1400), False),
    ("d_m", "1500〜1800m", _between("distance", 1500, 1800), False),
    ("d_l", "2000m以上", _between("distance", 2000), False),
    ("good", "良馬場", _eq("going", "良"), False),
    ("bad", "道悪 (稍重〜不良)", _isin("going", GOING_BAD), False),
    ("c_new", "新馬戦", _eq("cls", "新馬"), False),
    ("c_maiden", "未勝利戦", _eq("cls", "未勝利"), False),
    ("c_1", "1勝クラス", _eq("cls", "1勝"), False),
    ("c_2", "2勝クラス", _eq("cls", "2勝"), False),
    ("c_3", "3勝クラス", _eq("cls", "3勝"), False),
    ("c_op", "オープン・重賞", _eq("cls", "OP以上"), False),
    ("a2", "2歳戦", lambda r: r["age_max"] == 2, False),
    ("a3", "3歳限定", lambda r: (r["age_min"] == 3) & (r["age_max"] == 3), False),
    ("aold", "古馬混合 (3歳以上・4歳以上)", lambda r: r["age_max"] >= 4, False),
    ("mare", "牝馬限定", lambda r: r["mare_only"].astype(bool), False),
    ("hcap", "斤量差4kg以上 (ハンデ戦など)", _between("kin_range", 4), False),
    ("hcap6", "斤量差6kg以上", _between("kin_range", 6), False),
    ("local", "ローカル (札幌・函館・福島・新潟・小倉)", _isin("place", LOCAL), False),
    ("main", "中央4場 (東京・中山・京都・阪神)", _isin("place", {"東京", "中山", "京都", "阪神"}), False),
    ("chukyo", "中京", _eq("place", "中京"), False),
    ("winter", "冬 (12〜2月)", _isin("month", {12, 1, 2}), False),
    ("summer", "夏 (6〜8月)", _isin("month", {6, 7, 8}), False),
    ("r12", "最終レース (12R)", _eq("race_no", 12), False),
    ("r_early", "午前 (1〜4R)", _between("race_no", None, 4), False),
    ("fav3", "1番人気が3倍以上", _between("fav", 3.0), True),
    ("fav4", "1番人気が4倍以上", _between("fav", 4.0), True),
    ("fav2_", "1番人気が2倍未満", _between("fav", None, 1.99), True),
    ("fav2_6", "2番人気が6倍以上", _between("fav2", 6.0), True),
    ("lt10_6", "単勝10倍未満が6頭以上", _between("n_lt10", 6), True),
    ("lt10_3", "単勝10倍未満が3頭以下", _between("n_lt10", None, 3), True),
    ("lt20_9", "単勝20倍未満が9頭以上", _between("n_lt20", 9), True),
    ("top3_40", "上位3頭の支持が40%未満", _between("top3_share", None, 0.40), True),
    ("top3_60", "上位3頭の支持が60%以上", _between("top3_share", 0.60), True),
]
COND = {k: (label, f, uses_odds) for k, label, f, uses_odds in CONDITIONS}


def cond_masks(r):
    return {k: f(r).fillna(False).to_numpy(dtype=bool) for k, _, f, _ in CONDITIONS}


def match_rules(r, rules):
    """レース表 r の各レースが当てはまる法則 (番号のリスト) を返す。"""
    masks = cond_masks(r)
    out = [[] for _ in range(len(r))]
    for i, rule in enumerate(rules):
        m = np.ones(len(r), dtype=bool)
        for k in rule["keys"]:
            m &= masks[k]
        for j in np.flatnonzero(m):
            out[j].append(i)
    return out


def rule_label(keys):
    return "・".join(COND[k][0] for k in keys)


# ---------- サイトで使う ----------

class WinIndex:
    """馬ごとの勝利日の索引。ある日より前の勝ち数をすぐ引ける。"""

    def __init__(self, results):
        w = results[results["finish"] == 1]
        d = pd.to_datetime(w["date"]).values.astype("datetime64[D]").astype(np.int64)
        self._by = {}
        for hid, idx in pd.Series(range(len(w))).groupby(w["horse_id"].values).indices.items():
            self._by[hid] = np.sort(d[idx])

    def count(self, horse_ids, dates):
        d = pd.to_datetime(pd.Series(dates)).values.astype("datetime64[D]").astype(np.int64)
        out = np.zeros(len(d), dtype=float)
        for i, (hid, di) in enumerate(zip(horse_ids, d)):
            arr = self._by.get(hid)
            if arr is not None:
                out[i] = np.searchsorted(arr, di, side="left")
        return out


def load_wins(db_path):
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        res = pd.read_sql("SELECT horse_id, date, finish FROM results WHERE finish = 1", conn)
    return WinIndex(res)


def annotate(h, meta, wins):
    """出走馬の表 h からレースごとに当てはまる法則を調べる。

    戻り値: race_id を索引に million_n (当てはまる法則の数) と million_rules (法則番号 "0,3")。
    """
    rules = (meta or {}).get("rules") or []
    cols = ["million_n", "million_rules"]
    if not rules or h is None or h.empty:
        return pd.DataFrame(columns=cols)
    h = h.copy()
    if "is_nar" in h:
        h = h[h["is_nar"].fillna(0) == 0]
    if h.empty:
        return pd.DataFrame(columns=cols)
    h["prior_wins"] = wins.count(h["horse_id"].tolist(), h["date"].tolist())
    r = race_table(h)
    r["n"] = r["n"].clip(upper=18)   # 登録段階は18頭を超えることがある
    hits = match_rules(r, rules)
    return pd.DataFrame({"million_n": [len(x) for x in hits],
                         "million_rules": [",".join(map(str, x)) or None for x in hits]},
                        index=r.index)
