"""過去の同じレース (または同じ条件のレース) で、1〜3着に何番人気が来ていたか。

名前のあるレース (特別・重賞) は名前で、名前の無いレース (「3歳未勝利」など) は
同じ競馬場・芝ダ・距離・クラスで探す。表記の揺れ (第○回・(GI)・ステークス/S・全角数字・冠名) はそろえる。
"""
import re
import sqlite3
import unicodedata

import numpy as np
import pandas as pd

GENERIC = re.compile(r"^(障害)?(\d歳)(以上)?(新馬|未出走|未勝利|\d勝クラス|オープン|OP)$")
ABBR = [("ステークス", "S"), ("カップ", "C"), ("トロフィー", "T"), ("ハンデキャップ", "H"),
        ("ハンデ", "H"), ("スプリント", "スプリント")]
BANDS = [(1, 1, "1番人気"), (2, 3, "2-3番人気"), (4, 5, "4-5番人気"), (6, 9, "6-9番人気"), (10, 99, "10番人気以下")]
MAX_ROWS = 10


def norm(name):
    s = unicodedata.normalize("NFKC", name or "").replace(" ", "").replace("　", "")
    s = re.sub(r"^第\d+回", "", s)
    s = re.sub(r"\((G\d|GI{1,3}|J\.?G\d|J\.?GI{1,3}|L|OP|\d勝|オープン|リステッド)\)$", "", s)
    s = re.sub(r"\(重賞\)$", "", s)  # 天皇賞(春)/(秋) は分けたままにする
    for a, b in ABBR:
        s = s.replace(a, b)
    return s


def is_generic(name):
    return bool(GENERIC.match(norm(name)))


def load(conn):
    """1レース1行: 1〜3着の人気と馬名、三連単の払戻。"""
    r = pd.read_sql(
        "SELECT race_id, date, race_name, place, surface, distance, finish, popularity, horse_name "
        "FROM results WHERE is_nar=0 AND finish BETWEEN 1 AND 3", conn)
    fs = pd.read_sql("SELECT race_id, COUNT(*) fs FROM results WHERE is_nar=0 GROUP BY race_id", conn)
    tri = pd.read_sql("SELECT race_id, MAX(pay) tri FROM payouts WHERE bet_type='三連単' GROUP BY race_id", conn)
    r = r.sort_values(["race_id", "finish", "popularity"])
    g = r.groupby("race_id")
    races = g.agg(date=("date", "first"), race_name=("race_name", "first"), place=("place", "first"),
                  surface=("surface", "first"), distance=("distance", "first"))
    # 同着があるときも、着順の順に上から3頭を 1〜3着の枠に入れる (2着同着なら2頭目が3着の枠)
    r["slot"] = r.groupby("race_id").cumcount() + 1
    for k in (1, 2, 3):
        sub = r[r["slot"] == k].set_index("race_id")
        races[f"p{k}"] = sub["popularity"]
        races[f"n{k}"] = sub["horse_name"]
    races = races.join(fs.set_index("race_id")).join(tri.set_index("race_id")).reset_index()
    races["key"] = races["race_name"].map(norm)
    races["generic"] = races["race_name"].map(is_generic)
    return races


def _match_named(races, key):
    if len(key) < 3:
        return races.iloc[0:0]
    k = races["key"]
    return races[~races["generic"] & ((k == key) | k.str.endswith(key) | k.map(lambda x: len(x) >= 3 and key.endswith(x)))]


def summary(rows):
    out = {}
    for k in (1, 2, 3):
        v = pd.to_numeric(rows[f"p{k}"], errors="coerce").dropna()
        if v.empty:
            continue
        out[str(k)] = {"min": int(v.min()), "max": int(v.max()), "median": float(v.median()),
                       "bands": [int(((v >= lo) & (v <= hi)).sum()) for lo, hi, _ in BANDS]}
    allp = pd.concat([pd.to_numeric(rows[f"p{k}"], errors="coerce") for k in (1, 2, 3)]).dropna()
    out["top3"] = {"bands": [int(((allp >= lo) & (allp <= hi)).sum()) for lo, hi, _ in BANDS]}
    return out


def for_race(races, race_id, name, date, place, surface, distance, ctx=None):
    """そのレースより前の、同じレース (同じ条件) の一覧と人気の分布。
    ctx = (var_parts, cuts) があれば、人気どおりに決まりやすいか (荒れやすさ) も付ける。"""
    past = races[races["date"] < date]
    if is_generic(name):
        m = past[(past["key"] == norm(name)) & (past["place"] == place) & (past["surface"] == surface)
                 & (past["distance"] == distance)]
        mode = "cond"
        label = f"{place}{surface}{distance}m {norm(name)}"
    else:
        m = _match_named(past, norm(name))
        mode = "name"
        label = norm(name)
    if m.empty:
        return None
    m = m.sort_values("date", ascending=False)
    rows = [{"date": r.date, "place": r.place, "course": f"{r.surface}{_i(r.distance) or ''}", "fs": _i(r.fs),
             "pops": [_i(r.p1), _i(r.p2), _i(r.p3)], "names": [_s(r.n1), _s(r.n2), _s(r.n3)],
             "tri": _i(r.tri), "race_id": r.race_id}
            for r in m.head(MAX_ROWS).itertuples()]
    out = {"mode": mode, "label": label, "n": int(len(m)), "from": m["date"].min(),
           "summary": summary(m), "rows": rows}
    if ctx is not None and mode == "name" and "dev" in m:
        vp, cuts = ctx
        g = _grade(pd.Series([unicodedata.normalize("NFKC", name)])).iloc[0]
        # サイトのレース名には格が無いことがあるので、過去の回の格でも見る
        jushou = g in JUSHOU or bool(m["grade"].isin(JUSHOU).mean() >= 0.5)
        sc = upset_score(m, vp, jushou)
        if sc:
            sc["label"] = upset_label(sc["score"], cuts["jushou" if jushou else "other"])
            sc["group"] = "重賞" if jushou else "特別"
            out["upset"] = sc
    return out


def _s(v):
    return v if isinstance(v, str) else None


def _i(v):
    return None if v is None or v != v else int(v)


def cuts_for(r, vp):
    """荒れやすい / 堅い の境目 (重賞・特別それぞれで、見込みの上下2割)。"""
    out = {}
    for grp, m in (("jushou", r["grade"].isin(JUSHOU)), ("other", ~r["grade"].isin(JUSHOU))):
        s = r[m & r["dev"].notna() & ~r["generic"]]
        sc = [x["score"] for _, g in s.groupby("key") if (x := upset_score(g, vp, grp == "jushou"))]
        sc = np.array(sc or [0.0])
        out[grp] = {"hi": float(np.quantile(sc, 0.8)), "lo": float(np.quantile(sc, 0.2))}
    return out


def payload(conn, race_rows, races=None):
    """race_rows: [(race_id, name, date, place, surface, distance)] → {race_id: 結果}"""
    if not race_rows:
        return {}
    races = add_dev(races if races is not None else load(conn))
    vp = var_parts(races)
    ctx = (vp, cuts_for(races, vp))
    out = {}
    for rid, name, date, place, surface, distance in race_rows:
        if not name:
            continue
        res = for_race(races, rid, name, date, place, surface, distance, ctx)
        if res:
            out[rid] = res
    return out


# ---------- 人気どおりに決まらない (荒れやすい) レース ----------
# 1回ごとの「ずれ」= log10(三連単の払戻) − 同じ頭数での平均。三連単の払戻は 1〜3着の組み合わせへの
# 投票の少なさなので、人気から外れた決着ほど大きい。同じレースでも年ごとのばらつきが大きいので、
# 回数が少ないほど全体の平均へ寄せる (経験ベイズ: 重み = nτ² / (nτ² + σ²))。

GRADE_RE = r"\((J?G(?:III|II|I)|L|OP|重賞)\)\s*$"
JUSHOU = ("GI", "GII", "GIII")


def _grade(names):
    return names.str.extract(GRADE_RE)[0]


def add_dev(races):
    """races に grade・ずれ (dev)・1番人気の3着外・10番人気以下の頭数を足し、ずれの式を返す。"""
    r = races.copy()
    r["grade"] = _grade(r["race_name"])
    ok = r["tri"].notna() & r["fs"].notna() & r["p1"].notna() & ~r["generic"]
    lt = np.log10(r.loc[ok, "tri"].astype(float))
    fs = r.loc[ok, "fs"].astype(float)
    X = np.c_[np.ones(ok.sum()), fs, np.log(fs)]
    beta = np.linalg.lstsq(X, lt, rcond=None)[0]
    r["dev"] = np.nan
    r.loc[ok, "dev"] = lt - X @ beta
    pops = r[["p1", "p2", "p3"]]
    r["fav_in"] = (pops == 1).any(axis=1).where(r["p1"].notna())
    r["big"] = (pops >= 10).sum(axis=1).where(r["p1"].notna())
    return r


def var_parts(r, min_n=4):
    """同じレースの中のばらつき σ² と、レースごとの本当の差 τ² (重賞とそれ以外で別に)。"""
    out = {}
    for grp, m in (("jushou", r["grade"].isin(JUSHOU)), ("other", ~r["grade"].isin(JUSHOU))):
        s = r[m & r["dev"].notna() & ~r["generic"]]
        n = s.groupby("key").size()
        s = s[s["key"].isin(n[n >= min_n].index)]
        g = s.groupby("key")["dev"]
        if g.ngroups < 10:
            out[grp] = {"s2": 0.38, "t2": 0.02}
            continue
        s2 = float(g.var(ddof=1).mean())
        t2 = max(float(g.mean().var(ddof=1) - s2 / g.size().mean()), 0.0)
        out[grp] = {"s2": round(s2, 4), "t2": round(t2, 4), "races": int(g.ngroups)}
    return out


def upset_score(m, vp, jushou):
    """同じレースの過去 m から、ずれの見込み (log10) と表示用の数字。"""
    d = m["dev"].dropna()
    if len(d) < 3:
        return None
    p = vp["jushou" if jushou else "other"]
    n = len(d)
    w = n * p["t2"] / (n * p["t2"] + p["s2"]) if p["t2"] > 0 else 0.0
    score = float(d.mean() * w)
    tri = m["tri"].dropna()
    return {"score": round(score, 4), "mult": round(10 ** score, 2), "raw": round(float(d.mean()), 3),
            "w": round(w, 2), "n": n,
            "fav_in": int(m["fav_in"].fillna(False).astype(bool).sum()),
            "n_fav": int(m["fav_in"].notna().sum()),
            "big_runs": int((m["big"] > 0).sum()),
            "tri_med": int(tri.median()) if len(tri) else None,
            "tri_100k": int((tri >= 1e5).sum()), "tri_1m": int((tri >= 1e6).sum()),
            "tri_max": int(tri.max()) if len(tri) else None}


def canon(keys):
    """冠名の付け外し (「KBSファンタジーS」と「ファンタジーS」など) を、いちばん短い名前にそろえる。"""
    uniq = sorted(set(keys), key=len)
    to = {}
    for k in uniq:
        to[k] = next((b for b in uniq if len(b) >= 3 and len(b) < len(k) and k.endswith(b)), k)
    return keys.map(lambda k: to.get(to[k], to[k]))


def upset_label(score, cut):
    if score is None:
        return None
    return "荒れやすい" if score >= cut["hi"] else "堅い" if score <= cut["lo"] else "ふつう"


def jushou_table(conn, races=None):
    """重賞ごとの荒れやすさの一覧と、その見込みが翌年以降に当たっていたかの検証。"""
    r = add_dev(races if races is not None else load(conn))
    vp = var_parts(r)
    j = r[r["grade"].isin(JUSHOU) & r["dev"].notna()].copy()
    j["key"] = canon(j["key"])
    items = []
    for key, m in j.groupby("key"):
        s = upset_score(m, vp, True)
        if not s:
            continue
        last = m.sort_values("date").iloc[-1]
        s.update({"key": key, "name": norm(last["race_name"]), "grade": last["grade"],
                  "place": last["place"], "course": f"{last['surface']}{_i(last['distance']) or ''}",
                  "last": last["date"], "from": m["date"].min()})
        items.append(s)
    sc = np.array([x["score"] for x in items]) if items else np.array([0.0])
    cut = {"hi": round(float(np.quantile(sc, 0.8)), 4), "lo": round(float(np.quantile(sc, 0.2)), 4)}
    for x in items:
        x["label"] = upset_label(x["score"], cut)
    items.sort(key=lambda x: -x["score"])

    # 前向き検証: その年より前の回だけで見込みを出し、その年の結果と比べる
    rows = []
    for y in sorted(j["date"].str[:4].unique()):
        cur = j[j["date"].str[:4] == y]
        prev = j[j["date"].str[:4] < y]
        for key, m in prev.groupby("key"):
            s = upset_score(m, vp, True)
            c = cur[cur["key"] == key]
            if s and len(c):
                for t in c["tri"]:
                    rows.append((s["score"], float(t)))
    check = None
    if len(rows) >= 60:
        v = pd.DataFrame(rows, columns=["score", "tri"])
        v["q"] = pd.qcut(v["score"].rank(method="first"), 3, labels=["堅い側", "まんなか", "荒れやすい側"])
        g = v.groupby("q", observed=True)["tri"]
        check = {"n": int(len(v)), "corr": round(float(np.corrcoef(v["score"], np.log10(v["tri"]))[0, 1]), 3),
                 "groups": [{"label": str(k), "n": int(len(s)), "med": int(s.median()),
                             "r100k": round(float((s >= 1e5).mean()), 3), "r1m": round(float((s >= 1e6).mean()), 3)}
                            for k, s in g]}
    base = j.dropna(subset=["tri"])
    return {"items": items, "cut": cut, "var": vp, "check": check,
            "base": {"n": int(len(base)), "tri_med": int(base["tri"].median()),
                     "fav_in": round(float(base["fav_in"].mean()), 3),
                     "big": round(float((base["big"] > 0).mean()), 3),
                     "r100k": round(float((base["tri"] >= 1e5).mean()), 3)},
            "from": j["date"].min(), "to": j["date"].max()}
