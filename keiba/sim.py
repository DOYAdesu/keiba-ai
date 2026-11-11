"""展開シミュレーション (1レースを1万回走らせる)。

1回の試行:
  1. ペースを引く (想定ペースごとの実際のペースの出方。過去レースから)
  2. 脚質ごとにペースの向き不向きを強さに掛ける (過去の「AIの予想より走った/走らなかった」から)
  3. 1着を強さに比例して引き、2着・3着は強さを λ2・λ3 乗してから引く
     (人気馬が2・3着にも来すぎる Harville の偏りを直す。Benter / Lo の補正)

サイトでは同じ計算をブラウザで1万回まわす (web/static/app.js の simulate)。
ここでは物差し (fit_params) と、過去レースでの答え合わせ (validate) を作る。

  python -m keiba.sim            # 物差しを作り直して site_meta['sim_params'] に保存し、答え合わせを表示
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

from .scraper import DB_PATH

PACES = ["ハイ", "ミドル", "スロー"]
STYLES = ["逃げ", "先行", "差し", "追込"]
FIT_FROM = "2021-01-01"
VALID_FROM = "2026-05-19"


def _realized_pace(conn):
    r = pd.read_sql("SELECT race_id, surface, distance, pace_s3, pace_l3 FROM races "
                    "WHERE pace_s3 IS NOT NULL AND pace_l3 IS NOT NULL", conn)
    r["diff"] = r["pace_s3"] - r["pace_l3"]
    r["dev"] = r["diff"] - r.groupby(["surface", "distance"])["diff"].transform("median")
    r["real"] = np.where(r["dev"] <= -0.7, "ハイ", np.where(r["dev"] >= 0.7, "スロー", "ミドル"))
    return r.set_index("race_id")["real"]


def load_frame(conn):
    h = pd.read_sql(
        "SELECT p.race_id, p.horse_no, p.p_win, p.p_top3, p.style, p.finish, "
        "r.date, r.surface, r.pace AS pred_pace, r.field_size "
        "FROM site_preds p JOIN site_races r USING(race_id) "
        "WHERE r.is_nar=0 AND r.date>=? AND p.finish IS NOT NULL AND r.surface IN ('芝','ダ')",
        conn, params=(FIT_FROM,))
    h["real_pace"] = h["race_id"].map(_realized_pace(conn))
    h = h[h["real_pace"].notna() & h["p_win"].notna()]
    h["p_win"] = h["p_win"] / h.groupby("race_id")["p_win"].transform("sum")
    return h


def fit_pace_table(h):
    """想定ペース (と芝ダ) → 実際のペースの割合"""
    r = h.drop_duplicates("race_id")
    out = {}
    for surf, g in r.groupby("surface"):
        out[surf] = {}
        for pred, gg in g.groupby(g["pred_pace"].fillna("ミドル")):
            vc = gg["real_pace"].value_counts()
            # 少ないときは全体に寄せる (k=50)
            base = g["real_pace"].value_counts(normalize=True)
            n = len(gg)
            out[surf][pred] = {p: round(float((vc.get(p, 0) + 50 * base.get(p, 0)) / (n + 50)), 4)
                               for p in PACES}
    return out


def fit_style_shift(h):
    """脚質×実際のペースで、AIの複勝予想より何倍走ったか (対数)。ペースで平均すると0になるように。"""
    g = h[h["style"].isin(STYLES)].copy()
    g["y"] = (g["finish"] <= 3).astype(float)
    tab = g.groupby(["style", "real_pace"]).agg(y=("y", "sum"), e=("p_top3", "sum"), n=("y", "size"))
    base = g["real_pace"].value_counts(normalize=True)
    out = {}
    for s in STYLES:
        logs = {}
        for p in PACES:
            if (s, p) not in tab.index:
                logs[p] = 0.0
                continue
            row = tab.loc[(s, p)]
            # 少数のぶれを抑える (期待値に k=30 頭分の中立を足す)
            logs[p] = float(np.log((row["y"] + 30 * 0.3) / (row["e"] + 30 * 0.3)))
        mean = sum(base.get(p, 0) * logs[p] for p in PACES)
        out[s] = {p: round(logs[p] - mean, 4) for p in PACES}
    return out


def _seq_probs(w, lam2, lam3):
    """強さ w (合計1) から、各馬の 1着・2着・3着の確率を厳密に出す (λ補正つき)。"""
    n = len(w)
    p1 = w / w.sum()
    w2, w3 = w ** lam2, w ** lam3
    p2 = np.zeros(n)
    p3 = np.zeros(n)
    for i in range(n):
        if p1[i] <= 0:
            continue
        m = np.ones(n, bool)
        m[i] = False
        q2 = np.where(m, w2, 0) / w2[m].sum()
        p2 += p1[i] * q2
        for j in np.nonzero(q2 > 0)[0]:
            mm = m.copy()
            mm[j] = False
            q3 = np.where(mm, w3, 0) / w3[mm].sum()
            p3 += p1[i] * q2[j] * q3
    return p1, p2, p3


def fit_lambdas(h, grid2=np.arange(0.6, 1.01, 0.05), grid3=np.arange(0.5, 1.01, 0.05)):
    """実際の2着・3着の当てはまり (対数尤度) が最も良い λ2・λ3 を探す。"""
    races = []
    for _, g in h.groupby("race_id"):
        g = g.sort_values("finish")
        if len(g) < 5 or (g["finish"].iloc[:3].values != [1, 2, 3]).any():
            continue
        w = np.clip(g["p_win"].to_numpy(), 1e-4, None)
        races.append((w / w.sum(), 0, 1, 2))   # 着順で並べたので 0,1,2 が 1〜3着
    best2, best3 = 1.0, 1.0
    # λ2: 1着を除いた中から2着を引く確率
    def ll2(lam):
        s = 0.0
        for w, a, b, _ in races:
            x = w ** lam
            s += np.log(x[b] / (x.sum() - x[a]))
        return s
    def ll3(lam):
        s = 0.0
        for w, a, b, c in races:
            x = w ** lam
            s += np.log(x[c] / (x.sum() - x[a] - x[b]))
        return s
    best2 = max(grid2, key=ll2)
    best3 = max(grid3, key=ll3)
    return round(float(best2), 3), round(float(best3), 3), len(races)


def simulate(p_win, styles, pace_probs, params, n=10000, rng=None, keep=3):
    """n 回走らせて (着順 [n, keep] の馬の添字, ペース [n]) を返す。"""
    rng = rng or np.random.default_rng()
    p = np.clip(np.asarray(p_win, float), 1e-5, None)
    p = p / p.sum()
    lam2, lam3 = params["lam2"], params["lam3"]
    shift = params["shift"]
    k = params.get("k", 1.0)
    pr = np.array([pace_probs.get(x, 0) for x in PACES], float)
    paces = rng.choice(len(PACES), size=n, p=pr / pr.sum())
    H = len(p)
    out = np.zeros((n, keep), int)
    for pi, pace in enumerate(PACES):
        idx = np.nonzero(paces == pi)[0]
        if not len(idx):
            continue
        adj = np.array([shift.get(s, {}).get(pace, 0.0) if s else 0.0 for s in styles])
        w = p * np.exp(k * adj)
        w = w / w.sum()
        m = len(idx)
        taken = np.zeros((m, H), bool)
        for pos in range(keep):
            lam = 1.0 if pos == 0 else (lam2 if pos == 1 else lam3)
            ww = np.where(taken, 0.0, w ** lam)
            cum = np.cumsum(ww, axis=1)
            u = rng.random(m) * cum[:, -1]
            pick = (cum < u[:, None]).sum(axis=1)
            out[idx, pos] = pick
            taken[np.arange(m), pick] = True
    return out, paces


def race_pace_probs(params, surface, pred_pace):
    t = params["pace"].get(surface) or params["pace"].get("芝", {})
    return t.get(pred_pace or "ミドル") or t.get("ミドル") or {p: 1 / 3 for p in PACES}


def validate(h, params, n=4000, seed=1):
    """確認期間のレースで、シミュレーションの3着内率・組み合わせの当たり方を確かめる。"""
    rng = np.random.default_rng(seed)
    v = h[h["date"] >= VALID_FROM]
    ll_sim, ll_ai, ll_harv, nn = 0.0, 0.0, 0.0, 0
    combo_p, combo_hit, umaren_p, umaren_hit = [], [], [], []
    plain = dict(params, lam2=1.0, lam3=1.0, k=0.0)
    for rid, g in v.groupby("race_id"):
        g = g.sort_values("horse_no")
        if len(g) < 5:
            continue
        styles = g["style"].tolist()
        pp = race_pace_probs(params, g["surface"].iloc[0], g["pred_pace"].iloc[0])
        top, _ = simulate(g["p_win"].values, styles, pp, params, n=n, rng=rng)
        topH, _ = simulate(g["p_win"].values, styles, pp, plain, n=n, rng=rng)
        y = (g["finish"].to_numpy() <= 3).astype(float)
        H = len(g)
        def p3(t):
            c = np.bincount(t.ravel(), minlength=H) / len(t)
            return np.clip(c, 1e-3, 1 - 1e-3)
        for arr, acc in ((p3(top), "sim"), (p3(topH), "harv"),
                         (np.clip(g["p_top3"].to_numpy(), 1e-3, 1 - 1e-3), "ai")):
            ll = -(y * np.log(arr) + (1 - y) * np.log(1 - arr)).sum()
            if acc == "sim":
                ll_sim += ll
            elif acc == "harv":
                ll_harv += ll
            else:
                ll_ai += ll
        nn += H
        # 三連複・馬連の一番人気の組 (シミュレーションで最も多かった組) が当たったか
        fin = g["finish"].to_numpy()
        real3 = frozenset(np.nonzero(fin <= 3)[0])
        real2 = frozenset(np.nonzero(fin <= 2)[0])
        keys3 = pd.Series([frozenset(r) for r in top]).value_counts()
        keys2 = pd.Series([frozenset(r[:2]) for r in top]).value_counts()
        combo_p.append(keys3.iloc[0] / n)
        combo_hit.append(keys3.index[0] == real3)
        umaren_p.append(keys2.iloc[0] / n)
        umaren_hit.append(keys2.index[0] == real2)
    return {
        "races": len(combo_p),
        "logloss_top3": {"sim": round(ll_sim / nn, 4), "harville": round(ll_harv / nn, 4),
                         "ai": round(ll_ai / nn, 4)},
        "trio_top": {"p": round(float(np.mean(combo_p)), 4), "hit": round(float(np.mean(combo_hit)), 4)},
        "quinella_top": {"p": round(float(np.mean(umaren_p)), 4), "hit": round(float(np.mean(umaren_hit)), 4)},
    }


def fit_params(db_path=DB_PATH, log=print, check=True):
    conn = sqlite3.connect(db_path)
    try:
        h = load_frame(conn)
        fit = h[h["date"] < VALID_FROM]
        params = {"pace": fit_pace_table(fit), "shift": fit_style_shift(fit)}
        lam2, lam3, nr = fit_lambdas(fit[fit["date"] >= "2025-01-01"])
        params.update(lam2=lam2, lam3=lam3, k=1.0, n_fit=int(fit["race_id"].nunique()))
        log(f"λ2={lam2} λ3={lam3} ({nr}レース)")
        if check:
            best = None
            for k in (0.0, 0.5, 1.0, 1.5):
                rep = validate(h, dict(params, k=k), n=2000)
                log(f"k={k}: {rep}")
                if best is None or rep["logloss_top3"]["sim"] < best[1]["logloss_top3"]["sim"]:
                    best = (k, rep)
            params["k"] = best[0]
            params["check"] = validate(h, params, n=4000)
            log(f"採用 k={best[0]}: {params['check']}")
        conn.execute("CREATE TABLE IF NOT EXISTS site_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO site_meta VALUES ('sim_params', ?)",
                     (json.dumps(params, ensure_ascii=False),))
        conn.commit()
    finally:
        conn.close()
    return params


if __name__ == "__main__":
    p = fit_params()
    print(json.dumps({k: v for k, v in p.items() if k != "check"}, ensure_ascii=False, indent=1))
    sys.exit(0)
