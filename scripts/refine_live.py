"""当日の情報 (馬体重・直前オッズ・オッズの動き・クッション値) の効き目を比べる。

同じ確認期間 (2026-05-19〜、中央のみ) で次を比べる:
  A. 前日 (馬体重・馬場が未発表): 今のモデルに空欄で入れる vs 前日モデル (それらを使わずに学習)
  B. 市場モデル: 確定オッズで学習したモデルを「発走40分前のオッズ」で使うと、どれだけ落ちるか
     / 40分前のオッズで学習し直すと戻るか
  C. オッズの動き (前日最終→40分前) を足すと良くなるか
  D. クッション値・含水率を足すと良くなるか

  python scripts/refine_live.py            # 全部
  python scripts/refine_live.py A D        # 一部だけ
"""
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from keiba.features import (  # noqa: E402
    FEATURES, add_market_features, build_training_data, load_horses, load_results,
)
from keiba.scraper import DB_PATH  # noqa: E402
from keiba.train import _fit_lgbm  # noqa: E402

SPLIT = pd.Timestamp("2026-05-19")
START = pd.Timestamp("2020-01-01")
CACHE = ROOT / "data" / "refine_cache.pkl"
T0 = time.time()
PARTS = set(sys.argv[1:]) or set("ABCD")


def race_auc(race, s, y):
    x = pd.DataFrame({"race": race, "s": s, "y": y})
    x["r"] = x.groupby("race")["s"].rank(method="average")
    g = x.groupby("race")
    npos, n = g["y"].sum(), g["y"].count()
    nneg = n - npos
    spos = (x["r"] * x["y"]).groupby(x["race"]).sum()
    ok = (npos > 0) & (nneg > 0)
    auc = ((spos - npos * (npos + 1) / 2) / (npos * nneg))[ok].mean()
    top = x.sort_values("s", ascending=False).groupby("race").head(1)
    return auc, top["y"].mean()


def logloss(y, p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def load():
    if CACHE.exists() and CACHE.stat().st_mtime > (ROOT / "keiba" / "features.py").stat().st_mtime:
        X, meta = pd.read_pickle(CACHE)
    else:
        X, meta = build_training_data(load_results(), load_horses())
        pd.to_pickle((X, meta), CACHE)
    print(f"特徴量 {time.time() - T0:.0f}s / {len(X)}行", flush=True)
    return X, meta


def odds_table(conn, tag):
    """odds_hist の tag (pre/t40/...) を (race_id, horse_no) → 単勝オッズに展開する。"""
    rows = conn.execute("SELECT race_id, win FROM odds_hist WHERE tag=?", (tag,)).fetchall()
    out = {}
    for rid, win in rows:
        for i, v in enumerate((win or "").split(",")):
            if v:
                out[(rid, i + 1)] = float(v)
    return out


def baba_fit(meta, Xd, k=3.0):
    """その馬が似た馬場で走ったときの3着内率 (縮めたもの) − その馬の全体の3着内率。当日より前だけ。"""
    turf = (meta["surface"] == "芝").to_numpy() if "surface" in meta else None
    val = np.where(turf, Xd["cushion"].to_numpy(), Xd["moist"].to_numpy())
    tol = np.where(turf, 0.6, 1.5)
    df = pd.DataFrame({"h": meta["horse_id"].values, "d": meta["date"].values, "s": meta["surface"].values,
                       "v": val, "tol": tol, "y": meta["top3"].values})
    out = np.full(len(df), np.nan)
    for _, g in df.sort_values("d").groupby("h", sort=False):
        idx = g.index.to_numpy()
        d, s_, v, t, y = (g[c].to_numpy() for c in ("d", "s", "v", "tol", "y"))
        for j in range(1, len(idx)):
            if np.isnan(v[j]):
                continue
            past = (d[:j] < d[j]) & (s_[:j] == s_[j]) & ~np.isnan(v[:j])
            if past.sum() < 2:
                continue
            yp, vp = y[:j][past], v[:j][past]
            near = np.abs(vp - v[j]) <= t[j]
            allr = (yp.sum() + k * 0.3) / (len(yp) + k)
            nr = (yp[near].sum() + k * allr) / (near.sum() + k)
            out[idx[j]] = nr - allr
    return out


def evaluate(name, XX, meta, tr, valid, vj, rows, target="top3"):
    m = _fit_lgbm(XX[tr], meta[target][tr], XX[valid], meta[target][valid])
    p = m.predict_proba(XX[vj])[:, 1]
    y = meta[target][vj].values
    auc, top1 = race_auc(meta["race_id"][vj].values, p, y)
    rows.append({"比較": name, "対象": target, "AUC": round(auc, 4), "1位の的中率": round(top1, 4),
                 "logloss": round(logloss(y, p), 4)})
    print(rows[-1], f"{time.time() - T0:.0f}s", flush=True)
    return m


def main():
    X, meta = load()
    meta = meta.reset_index(drop=True)
    X = X.reset_index(drop=True)
    valid = (meta["date"] >= SPLIT).to_numpy()
    vj = valid & (meta["is_nar"] == 0).to_numpy()
    tr = ((meta["date"] >= START) & (meta["date"] < SPLIT)).to_numpy()
    rows = []
    conn = sqlite3.connect(DB_PATH)

    if "A" in PARTS:
        print("== A. 前日モデル ==")
        day_cols = ["weight", "weight_diff", "going_code"]
        for target in ("top3", "win"):
            m = evaluate("当日 (馬体重・馬場あり)", X, meta, tr, valid, vj, rows, target)
            Xp = X.copy()
            Xp.loc[vj, day_cols] = np.nan
            p = m.predict_proba(Xp[vj])[:, 1]
            y = meta[target][vj].values
            auc, top1 = race_auc(meta["race_id"][vj].values, p, y)
            rows.append({"比較": "前日: 今のモデルに空欄で入れる", "対象": target, "AUC": round(auc, 4),
                         "1位の的中率": round(top1, 4), "logloss": round(logloss(y, p), 4)})
            print(rows[-1], flush=True)
            pre = [c for c in FEATURES if c not in day_cols]
            evaluate("前日: 前日モデル (馬体重・馬場なしで学習)", X[pre], meta, tr, valid, vj, rows, target)

    if "B" in PARTS or "C" in PARTS:
        print("== B/C. 直前オッズ ==")
        key = list(zip(meta["race_id"], meta["horse_no"].astype(int)))
        o40 = odds_table(conn, "t40")
        opre = odds_table(conn, "pre")
        meta["odds_t40"] = [o40.get(k, np.nan) for k in key]
        meta["odds_pre"] = [opre.get(k, np.nan) for k in key]
        have = meta["odds_t40"].notna().to_numpy()
        print(f"40分前のオッズがある行: {have.mean():.1%} (確認期間 {have[vj].mean():.1%})")
        trh, vh, vjh = tr & have, valid & have, vj & have
        Xf = add_market_features(X, meta["odds"], meta["race_id"])
        X40 = add_market_features(X, meta["odds_t40"], meta["race_id"])
        for target in ("top3", "win"):
            m = _fit_lgbm(Xf[trh], meta[target][trh], Xf[vh], meta[target][vh])
            y = meta[target][vjh].values
            for nm, XX in (("確定オッズで学習→確定オッズで予想 (今の答え合わせ)", Xf),
                           ("確定オッズで学習→40分前オッズで予想 (実際の運用)", X40)):
                p = m.predict_proba(XX[vjh])[:, 1]
                auc, top1 = race_auc(meta["race_id"][vjh].values, p, y)
                rows.append({"比較": nm, "対象": target, "AUC": round(auc, 4),
                             "1位の的中率": round(top1, 4), "logloss": round(logloss(y, p), 4)})
                print(rows[-1], flush=True)
            evaluate("40分前オッズで学習→40分前オッズで予想", X40, meta, trh, vh, vjh, rows, target)
            if "C" in PARTS:
                Xc = X40.copy()
                Xc["odds_move"] = np.log(meta["odds_t40"] / meta["odds_pre"])
                # レースの中で相対的にどれだけ売れたか
                Xc["odds_move_rel"] = Xc["odds_move"] - Xc.groupby(meta["race_id"])["odds_move"].transform("mean")
                evaluate("＋オッズの動き (前日最終→40分前)", Xc, meta, trh, vh, vjh, rows, target)
            # 市場の値だけ (オッズ順) も並べておく
            p = 1 / meta["odds_t40"][vjh].values
            auc, top1 = race_auc(meta["race_id"][vjh].values, p, y)
            rows.append({"比較": "参考: 40分前の1番人気", "対象": target, "AUC": round(auc, 4),
                         "1位の的中率": round(top1, 4), "logloss": None})

    if "D" in PARTS:
        print("== D. クッション値・含水率 ==")
        from keiba.baba import race_baba
        rr = pd.read_sql("SELECT race_id, date, place, surface FROM races", conn)
        b = race_baba(conn, rr).set_index("race_id")
        Xd = X.copy()
        for c in ("cushion", "moist", "moist_4c"):
            Xd[c] = meta["race_id"].map(b[c]).astype(float).values
        print(f"クッション値がある行: {Xd['cushion'].notna().mean():.1%} / 含水率: {Xd['moist'].notna().mean():.1%}")
        for target in ("top3",):
            evaluate("今の特徴量", X, meta, tr, valid, vj, rows, target)
            evaluate("＋クッション値・含水率", Xd, meta, tr, valid, vj, rows, target)
        # 馬との相性: 似た馬場 (芝はクッション値±0.6、ダートは含水率±1.5) での過去の3着内率 − 全体の3着内率
        Xd2 = X.copy()
        Xd2["baba_fit"] = baba_fit(meta, Xd)
        print(f"馬場の相性がある行: {Xd2['baba_fit'].notna().mean():.1%}")
        evaluate("＋似た馬場での得手不得手", Xd2, meta, tr, valid, vj, rows, "top3")
        evaluate("＋似た馬場での得手不得手 (勝率)", Xd2, meta, tr, valid, vj, rows, "win")
        evaluate("今の特徴量 (勝率)", X, meta, tr, valid, vj, rows, "win")

    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
