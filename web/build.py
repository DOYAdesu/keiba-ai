"""サイト表示用の事前計算バッチ。

DB に溜まっているレース結果を全部モデルに通し、サイトが「読むだけ」で
描画できる形 (site_races / site_preds) に落とす。
画面側で推論しないので表示が速く、モデルを差し替えたらここを流し直せばよい。

    .venv\\Scripts\\python -m web.build            # 全期間
    .venv\\Scripts\\python -m web.build --from 2026-05-20
"""
import argparse
import sqlite3

import joblib
import numpy as np
import pandas as pd

from keiba.ev import fukusho_ev, load_fukusho_payouts, predict_fukusho_odds
from keiba.features import (
    add_market_features, build_training_data, load_horses, load_results,
)
from keiba.scraper import DB_PATH
from keiba.train import MODEL_PATH
from keiba.upset import label as upset_label
from keiba import upset as upset_mod
from keiba.upset import payout_probs
from web import insight, million

# 推奨 (堅軸) の条件: 複勝率が高く、2番手に対して抜けている本命
REC_MIN_TOP3 = 0.75
REC_MIN_GAP = 0.20
# ★ (妙味) の条件: 複勝の期待値が損益分岐を超えている
STAR_MIN_EV = 1.00

MARKS = ["◎", "○", "▲", "△", "△"]

# 収集バッチと同時に走っても現行テーブルを壊さないよう、
# いったん _new に作ってから最後に差し替える。
SCHEMA = """
DROP TABLE IF EXISTS site_races_new;
DROP TABLE IF EXISTS site_preds_new;

CREATE TABLE site_races_new (
    race_id     TEXT PRIMARY KEY,
    date        TEXT,
    place       TEXT,
    race_no     INTEGER,
    race_name   TEXT,
    surface     TEXT,
    distance    INTEGER,
    going       TEXT,
    weather     TEXT,
    is_nar      INTEGER,
    field_size  INTEGER,
    post_time   TEXT,
    grade       TEXT,
    confidence  REAL,
    is_rec      INTEGER,
    is_star     INTEGER,
    p_hit_100k  REAL,
    p_hit_1m    REAL,
    max_payout  REAL,
    upset_label TEXT,
    reason      TEXT,
    in_sample   INTEGER,
    top_horse_no   INTEGER,
    top_horse_name TEXT,
    top_p_win      REAL,
    top_p_top3     REAL,
    top_ev         REAL,
    top_odds       REAL,
    top_finish     INTEGER,
    top_fuku_pay   INTEGER,
    trifecta_pay   INTEGER,
    top_index      INTEGER,
    pace           TEXT,
    pay_note       TEXT,
    winner_no      INTEGER,
    winner_name    TEXT,
    stage          TEXT,
    upset_basis    TEXT,
    million_n      INTEGER,
    million_rules  TEXT
);

CREATE TABLE site_preds_new (
    race_id    TEXT,
    rank       INTEGER,
    mark       TEXT,
    horse_no   INTEGER,
    waku       INTEGER,
    horse_name TEXT,
    sire       TEXT,
    dam_sire   TEXT,
    sex        TEXT,
    age        INTEGER,
    kinryo     REAL,
    jockey_name TEXT,
    trainer_name TEXT,
    weight     INTEGER,
    weight_diff INTEGER,
    odds       REAL,
    popularity INTEGER,
    p_win      REAL,
    p_top3     REAL,
    fair_odds  REAL,
    fuku_odds  REAL,
    ev_fuku    REAL,
    finish     INTEGER,
    time_sec   REAL,
    agari      REAL,
    corner1    INTEGER,
    ability_idx INTEGER,
    style      TEXT,
    c_spd INTEGER, c_agari INTEGER, c_recent INTEGER, c_career INTEGER,
    c_dist INTEGER, c_course INTEGER, c_jockey INTEGER, c_trainer INTEGER,
    c_sire INTEGER, c_going INTEGER,
    PRIMARY KEY (race_id, horse_no)
);
"""

# 書き込みが終わってから一気に差し替える (索引名は全体で一意なので作成はこの後)
SWAP = """
DROP TABLE IF EXISTS site_races;
DROP TABLE IF EXISTS site_preds;
ALTER TABLE site_races_new RENAME TO site_races;
ALTER TABLE site_preds_new RENAME TO site_preds;
CREATE INDEX idx_site_races_date ON site_races(date);
CREATE INDEX idx_site_preds_race ON site_preds(race_id);
"""


def _calibrate(bundle, key, prob):
    ir = bundle.get("calibrators", {}).get(key)
    return np.clip(ir.predict(prob), 0.0, 1.0) if ir is not None else prob


def score_all(db_path=DB_PATH, model_path=MODEL_PATH, date_from=None):
    """全レースを推論し、1行=1頭の DataFrame を返す。"""
    bundle = joblib.load(model_path)
    if "model_market_win" not in bundle:
        raise RuntimeError("旧形式のモデルです。先に学習を実行し直してください。")

    df = load_results(db_path)
    if df.empty:
        raise RuntimeError("対象のレースがありません。")

    # 特徴量は必ず全期間で作る。通算成績や適性は過去全部を見るので、
    # ここで期間を絞ると「デビューしたて」の馬だらけになってしまう。
    X, meta = build_training_data(df)
    # 騎手×調教師は過去全部を見るので、期間で絞る前に計算する
    meta = meta.copy()
    meta["jt_n"], meta["jt_rate"] = insight.jt_rates(meta)
    comps = insight.prior_components(meta)
    for c in comps.columns:
        meta[c] = comps[c]
    if date_from:
        keep = meta["date"] >= pd.Timestamp(date_from)
        X, meta = X[keep], meta[keep]
        if meta.empty:
            raise RuntimeError(f"{date_from} 以降のレースがありません。")
    Xm = add_market_features(X, meta["odds"], meta["race_id"])

    out = meta.copy()
    ped = load_horses(db_path).drop_duplicates("horse_id").set_index("horse_id")
    out["sire"] = out["horse_id"].map(ped["sire_name"]) if not ped.empty else None
    out["dam_sire"] = out["horse_id"].map(ped["dam_sire_name"]) if not ped.empty else None
    # 表示は市場モデル (オッズ込み・的中率重視) を使う。predict.py と同じ方針。
    out["p_win_raw"] = _calibrate(
        bundle, "market_win", bundle["model_market_win"].predict_proba(Xm)[:, 1]
    )
    # 校正器 (isotonic) は上限に張り付いて 1.0 を返すことがある。
    # 「複勝率100%」は実態を超えた表示になるので頭を押さえる。
    out["p_top3"] = np.clip(
        _calibrate(
            bundle, "market_top3", bundle["model_market_top3"].predict_proba(Xm)[:, 1]
        ),
        0.01, 0.97,
    )
    # 勝率はレース内で合計1になるよう正規化する
    out["p_win"] = out.groupby("race_id")["p_win_raw"].transform(lambda s: s / s.sum())
    pa = _calibrate(bundle, "win", bundle["model_win"].predict_proba(X)[:, 1])
    out["p_win_ability"] = pd.Series(pa, index=out.index).groupby(
        out["race_id"]).transform(lambda s: s / s.sum())
    out["fair_odds"] = 1.0 / out["p_win"].replace(0, np.nan)

    out["fuku_odds"] = predict_fukusho_odds(bundle.get("fuku_odds_model"), out)
    out["ev_fuku"] = fukusho_ev(out["p_top3"], out["fuku_odds"])

    # 能力指数のもと: オッズを使わない能力モデルの複勝率
    out["p_ability"] = _calibrate(
        bundle, "top3", bundle["model_top3"].predict_proba(X)[:, 1]
    )
    out["style_ratio"] = X["avg_corner1_ratio5"].values
    for col, xcol in insight.COMPONENT_FROM_X.items():
        out[col] = X[xcol].values
    out["style"] = [insight.style_of(v) for v in out["style_ratio"]]

    out = out.sort_values(["race_id", "p_win", "p_top3"], ascending=[True, False, False])
    out["rank"] = out.groupby("race_id").cumcount() + 1
    out["mark"] = [MARKS[r - 1] if r <= len(MARKS) else "" for r in out["rank"]]
    return out, str(bundle.get("split_date") or "")


def _reason(top, second, field_size, pop_stats=None, extras=()):
    """「根拠」テキストを組み立てる。

    モデルの評価 → ★妙味 → 人気帯の過去実績 → 騎手×調教師 → 展開 の順。
    """
    parts = [
        f"モデル勝率{top['p_win'] * 100:.0f}%・複勝率{top['p_top3'] * 100:.0f}%の"
        + ("抜けた本命" if (top["p_top3"] - second) >= REC_MIN_GAP else "本命")
    ]
    if top["p_top3"] >= 0.70:
        parts[0] += "（複勝向き）"
    if pd.notna(top.get("ev_fuku")) and top["ev_fuku"] >= STAR_MIN_EV:
        parts.append(
            f"★複勝期待値{top['ev_fuku']:.2f}"
            f"（予測複勝{top['fuku_odds']:.1f}倍）"
        )
    pop_txt = insight.popularity_text(top.get("popularity"), pop_stats or {})
    if pop_txt:
        parts.append(pop_txt)
    if pd.notna(field_size) and field_size <= 10:
        parts.append(f"少頭数{int(field_size)}頭")
    parts += [e for e in extras if e]
    return " ／ ".join(parts)


def enrich(g, as_of, ctx):
    """1レースぶんの追加情報 (展開・騎手×調教師・指数・脚質) を計算する。

    g: そのレースの全頭 (style_ratio / jt_n / jt_rate / horse_id / ability_idx を持つ)
    ctx: pace_map (race_id→実際のペース), horse_hist (horse_id→過去成績df) など
    返り値: (根拠に足す文の list, レースに足す列の dict)
    """
    g = g.sort_values("rank")
    top = g.iloc[0]
    pace, n_front = insight.predict_pace(g.get("style_ratio", pd.Series(dtype=float)))
    style = insight.style_of(top.get("style_ratio"))

    hist = ctx["horse_hist"](top.get("horse_id"))
    if hist is not None and len(hist):
        hist = hist[hist["date"] < as_of]
    same = insight.same_pace_good(hist, pace, ctx["pace_map"])

    jt_txt = None
    if "jt_rate" in g.columns:
        ok = g["jt_n"].fillna(0) >= insight.JT_MIN_RIDES
        ranks = g.loc[ok, "jt_rate"].rank(ascending=False, method="min")
        rank = ranks.get(top.name) if top.name in ranks.index else None
        jt_txt = insight.jt_text(top.get("jt_rate", np.nan), top.get("jt_n"), rank)

    extras = [jt_txt, insight.pace_text(pace, n_front, style, same)]
    cols = {
        "top_index": (None if pd.isna(top.get("ability_idx", np.nan))
                      else int(top["ability_idx"])),
        "pace": pace,
    }
    return extras, cols


# オッズ発表前に見積もるとき、実際に走れるのはフルゲートまで。
# 特別登録は50頭を超えることもあるので、出走しそうな上位だけを見る。
AI_MAX_FIELD = 18


def _ai_upset(p_win):
    """オッズを使わず、AIの勝率だけから荒れ度を出す (生の値)。

    「市場がAIと同じ見立て」と仮定してAIの勝率から単勝オッズを作る (払戻率80%)。
    登録段階は登録馬が多すぎるので、出走しそうな上位18頭に絞る。
    オッズが作り物なので「想定最高配当」は出さない (打ち切りの位置しか表さない)。
    """
    p = pd.Series(p_win, dtype=float).dropna().nlargest(AI_MAX_FIELD)
    if len(p) < 4:
        return {}
    p = p / p.sum()
    return payout_probs(p, 0.8 / p.clip(lower=1e-4), with_payout=False)


# オッズ発表前は「10万超」の確率を出さない。
#
# 特別登録50頭から上位18頭を選ぶと、残るのは実力の近い馬ばかりで、
# AIの勝率がほぼ横一線になる。三連単の全組み合わせの配当が10万円前後に
# 固まるため「10万超の確率」が94〜100%に張り付き、実際に起こりうる上限
# (18頭立ての実測34%) を大きく超えてしまう。単調な較正では直せない。
# 100万超はしきい値がずっと裾にあるので張り付かず、検証でも
# 推定0.2/1.1/2.0/2.8/3.6% に対し実測0.3/0.9/2.0/2.9/5.5% と使える。
AI_THRESHOLDS = (1_000_000,)


def _map_upset(raw, ai_calib):
    """AIだけの荒れ度を、実際の三連単配当に合わせて較正する。

    以前は「オッズ込みの荒れ度」の分位点に合わせていたが、オッズ発表前に
    予想するのは多頭数の特別戦ばかりなので、全レースの分位点に当てはめると
    上端に張り付き、出すぎた数字になっていた。実配当を正解にする。
    """
    out = {100_000: None, 1_000_000: None, "max_payout": None}
    for t in AI_THRESHOLDS:
        v = raw.get(f"raw_{t}")
        if v is not None and ai_calib:
            out[t] = upset_mod.calibrate(v, t, ai_calib)
    return out


def _upset(g, upset_calib=None):
    """荒れ度 (高額配当の出やすさ)。オッズが出ていなければAIの勝率から見積もる。"""
    calib = upset_calib or {}
    if g["odds"].notna().sum() >= max(4, len(g) // 2):
        return payout_probs(g["p_win"], g["odds"],
                            calib=calib.get("market") or None), "market"
    return _map_upset(_ai_upset(g["p_win"]), calib.get("ai")), "ai"


def race_row(race_id, g, info, fuku_pay=None, trifecta_pay=None, in_sample=0,
             pop_stats=None, extras=(), extra_cols=None, upset_calib=None):
    """1レースぶんの site_races 行を作る。

    g: そのレースの全頭 (rank 昇順)。info: 日付・場・距離などのレース情報。
    終わったレースにも、これからのレースにも同じ形で使う。
    """
    g = g.sort_values("rank")
    top = g.iloc[0]
    second_top3 = float(g.iloc[1]["p_top3"]) if len(g) > 1 else 0.0
    gap = float(top["p_top3"]) - second_top3
    upset, upset_basis = _upset(g, upset_calib)
    finish = top.get("finish")

    # 1着馬 (終わったレースだけ)
    winner_no = winner_name = None
    if "finish" in g.columns:
        w = g[g["finish"] == 1]
        if len(w):
            winner_no = int(w.iloc[0]["horse_no"])
            winner_name = w.iloc[0]["horse_name"]
    n_same = (int((g["finish"] == finish).sum())
              if finish is not None and not pd.isna(finish) else 0)

    row = {
        "race_id": race_id,
        "date": info["date"],
        "place": info["place"],
        "race_no": info.get("race_no"),
        "race_name": info.get("race_name"),
        "surface": info.get("surface"),
        "distance": info.get("distance"),
        "going": info.get("going"),
        "weather": info.get("weather"),
        "is_nar": int(info.get("is_nar") or 0),
        "field_size": int(len(g)),
        "post_time": info.get("post_time"),
        "grade": info.get("grade"),
        "confidence": round(float(top["p_top3"]) * 0.7 + gap * 1.5, 3),
        "is_rec": int(top["p_top3"] >= REC_MIN_TOP3 and gap >= REC_MIN_GAP),
        "is_star": int(pd.notna(top["ev_fuku"]) and top["ev_fuku"] >= STAR_MIN_EV),
        "p_hit_100k": upset[100_000],
        "p_hit_1m": upset[1_000_000],
        "max_payout": upset["max_payout"],
        # 較正をあとから学習し直すための生の推定 (書き込む前に落とす)
        "_raw_100000": upset.get("raw_100000"),
        "_raw_1000000": upset.get("raw_1000000"),
        "upset_label": upset_label(upset[1_000_000]),
        "upset_basis": upset_basis,
        "reason": _reason(top, second_top3, len(g), pop_stats, extras),
        "in_sample": int(in_sample),
        "top_horse_no": int(top["horse_no"]),
        "top_horse_name": top["horse_name"],
        "top_p_win": float(top["p_win"]),
        "top_p_top3": float(top["p_top3"]),
        "top_ev": None if pd.isna(top["ev_fuku"]) else float(top["ev_fuku"]),
        "top_odds": None if pd.isna(top["odds"]) else float(top["odds"]),
        "top_finish": None if finish is None or pd.isna(finish) else int(finish),
        "top_fuku_pay": fuku_pay,
        "trifecta_pay": trifecta_pay,
        "top_index": None,
        "pace": None,
        "pay_note": insight.pay_note(finish, fuku_pay, n_same),
        "winner_no": winner_no,
        "winner_name": winner_name,
    }
    row.update(extra_cols or {})
    return row


# site_preds に入れる列 (upcoming 側と揃える)
PRED_COLS = ["race_id", "rank", "mark", "horse_no", "waku", "horse_name",
             "sire", "dam_sire", "sex", "age",
             "kinryo", "jockey_name", "trainer_name",
             "weight", "weight_diff", "odds", "popularity",
             "p_win", "p_top3", "fair_odds", "fuku_odds", "ev_fuku", "finish",
             "time_sec", "agari", "corner1", "ability_idx", "style"] + ["c_spd", "c_agari", "c_recent", "c_career", "c_dist", "c_course", "c_jockey", "c_trainer", "c_sire", "c_going"]


def _fit_upset_calib(races, ai_raw, progress):
    """荒れ度を実際の三連単配当に合わせて較正する (ロジスティック)。

    オッズ込み (market) と、オッズ発表前のAIのみ (ai) を別々に合わせる。
    返り値は {"market": {"100000": [a, b], ...}, "ai": {...}} で、
    site_meta に入れて upcoming 側からも使う。

    較正は学習期間のレースだけで合わせる。検証期間 (_upset_check) を混ぜると
    「合っているか」の答えを先に見てしまうことになるため。
    """
    pay = pd.to_numeric(races["trifecta_pay"], errors="coerce")
    known = pay.notna() & (pd.to_numeric(races["in_sample"], errors="coerce") == 1)
    out = {"market": {}, "ai": {}}
    if known.sum() < 500:
        progress("配当の分かるレースが少ないため荒れ度の較正は見送ります。")
        return out
    for t in (100_000, 1_000_000):
        hit = (pay >= t).astype(float)
        raws = {
            "market": pd.to_numeric(races.get(f"_raw_{t}"), errors="coerce"),
            "ai": pd.Series([(r or {}).get(f"raw_{t}") for r in ai_raw],
                            index=races.index, dtype=float),
        }
        for name, raw in raws.items():
            m = known & raw.notna() & (raw > 0)
            if m.sum() < 500:
                continue
            a, b = upset_mod.fit_calibration(raw[m], hit[m])
            out[name][str(t)] = [a, b]
            est = np.mean([upset_mod.calibrate(v, t, {str(t): (a, b)})
                           for v in raw[m]])
            progress(f"荒れ度の較正 {name:>6} {t:>9,}円以上: "
                     f"a={a:+.3f} b={b:+.3f} / 平均の推定 {est:.2%} "
                     f"実測 {hit[m].mean():.2%} (n={int(m.sum()):,})")
    return out


def _upset_conditions(races, pay):
    """100万超が「どんなレースで出たか」を条件別に数える (サイトの説明に出す)。

    手書きだと収集が進むたびに古くなるので、毎回実配当から作り直す。
    """
    d = pd.DataFrame({
        "hit": (pay >= 1_000_000).astype(float),
        "fs": pd.to_numeric(races["field_size"], errors="coerce"),
        "dist": pd.to_numeric(races["distance"], errors="coerce"),
        "surface": races["surface"],
        "going": races["going"],
    })
    base = float(d["hit"].mean())
    axes = [
        ("頭数", pd.cut(d["fs"], [0, 9, 12, 15, 99],
                        labels=["9頭以下", "10-12頭", "13-15頭", "16頭以上"])),
        ("距離", pd.cut(d["dist"], [0, 1300, 1700, 2100, 9999],
                        labels=["1300m以下", "1400-1700m", "1800-2100m", "2200m以上"])),
        ("芝ダ", d["surface"]),
        ("馬場", d["going"]),
    ]
    out = {"base": round(base, 5), "n": int(len(d)), "axes": []}
    for name, key in axes:
        items = []
        for k, g in d.groupby(key, observed=True):
            if len(g) < 100:
                continue
            rate = float(g["hit"].mean())
            items.append({"label": str(k), "n": int(len(g)),
                          "hits": int(g["hit"].sum()),
                          "rate": round(rate, 5),
                          "lift": round(rate / base, 2) if base else None})
        if items:
            items.sort(key=lambda x: -x["rate"])
            out["axes"].append({"name": name, "items": items})
    return out


def _upset_check(races, ai_raw, calib, split_date, progress):
    """荒れ度が実際の配当とどれだけ合っているかを、検証期間で5段階に分けて確かめる。

    サイトの説明文に出す数字もここで作る (手書きだと古くなるので)。
    """
    pay = pd.to_numeric(races["trifecta_pay"], errors="coerce")
    report = {"base_rate": None, "n_pay": int(pay.notna().sum()), "paths": {}}
    known = pay.notna()
    if known.sum():
        report["base_rate"] = round(float((pay[known] >= 1_000_000).mean()), 5)
        report["conditions"] = _upset_conditions(races[known], pay[known])
    for key, name, raw in (
        ("market", "オッズ込み",
         pd.to_numeric(races.get("_raw_1000000"), errors="coerce")),
        ("ai", "オッズ前(AIのみ)",
         pd.Series([(r or {}).get("raw_1000000") for r in ai_raw],
                   index=races.index, dtype=float)),
    ):
        ab = (calib.get(key) or {}).get("1000000")
        d = pd.DataFrame({"raw": raw, "pay": pay, "date": races["date"]}).dropna()
        if split_date:
            d = d[d["date"] > split_date]
        if len(d) < 200 or not ab:
            continue
        d["est"] = [upset_mod.calibrate(v, 1_000_000, {"1000000": ab}) for v in d["raw"]]
        d["hit"] = d["pay"] >= 1_000_000
        try:
            d["q"] = pd.qcut(d["est"], 5, labels=["最も堅い", "堅い", "普通",
                                                  "荒れ気味", "最も荒れる"])
        except ValueError:
            continue
        qs = [{"label": str(k), "n": int(len(v)),
               "est": round(float(v["est"].mean()), 5),
               "act": round(float(v["hit"].mean()), 5)}
              for k, v in d.groupby("q", observed=True)]
        report["paths"][key] = {
            "n_valid": int(len(d)),
            "act": round(float(d["hit"].mean()), 5),
            "quintiles": qs,
        }
        progress(f"荒れ度の検証 {name} ({len(d):,}R): " + " | ".join(
            f"{q['label']} 推定{q['est']:.1%}/実測{q['act']:.1%}({q['n']}R)" for q in qs))
    return report


def _pace_check(races, pace_map, progress):
    """想定ペースが実際のペースとどれだけ噛み合っているかをログに出す。"""
    df = races[["race_id", "pace"]].dropna().copy()
    df["actual"] = df["race_id"].map(pace_map)
    df = df.dropna()
    if df.empty:
        return
    tab = pd.crosstab(df["pace"], df["actual"], normalize="index").round(2)
    progress("想定ペース × 実際のペース (行ごとの割合):\n" + tab.to_string())


def build(db_path=DB_PATH, model_path=MODEL_PATH, date_from=None, progress=print):
    preds, split_date = score_all(db_path, model_path, date_from)
    progress(f"{preds['race_id'].nunique()}レース / {len(preds)}頭を推論しました。")

    # 能力指数: 全出走馬の中での位置。対応表は upcoming でも使うので保存する
    table = insight.index_table(preds["p_ability"])
    preds["ability_idx"] = insight.to_index(preds["p_ability"], table)
    comp_tables = insight.component_tables(preds)
    scores = insight.component_scores(preds, comp_tables)
    for c in scores.columns:
        preds[c] = scores[c]

    # 指数 = AI能力モデル7割 + 内訳の合成3割。合成の重みは学習期間だけで決める
    top3 = (preds["finish"] <= 3).astype(float).where(preds["finish"].notna())
    fit_mask = (preds["date"] < pd.Timestamp(split_date)).to_numpy() if split_date \
        else preds["finish"].notna().to_numpy()
    index_blend = insight.fit_index_blend(preds["ability_idx"], scores, top3, fit_mask)
    preds["ability_idx"] = insight.apply_index_blend(preds["ability_idx"], scores, index_blend)
    report = insight.index_report(
        preds["ability_idx"], scores, preds["race_id"], preds["finish"],
        ((preds["date"] >= pd.Timestamp(split_date)) if split_date else preds["finish"].notna())
        & (preds["is_nar"].fillna(0) == 0))   # サイトに載せている中央だけで答え合わせ
    report["from"] = split_date
    progress(f"指数の検証 ({split_date} 以降 {report['n_races']}レース): "
             f"AUC {report['auc']['指数']} / 指数1位の複勝率 {report['top1_top3']:.1%}"
             f" / 指数帯 " + ", ".join(f"{b['band']}:{b['top3']:.0%}" for b in report["bands"]))

    # 推奨の人気帯別の実績 (根拠の文に使う)
    tops = preds[preds["rank"] == 1].set_index("race_id")
    second = preds[preds["rank"] == 2].set_index("race_id")["p_top3"]
    gap = tops["p_top3"] - second.reindex(tops.index).fillna(0)
    rec_tops = tops[(tops["p_top3"] >= REC_MIN_TOP3) & (gap >= REC_MIN_GAP)]
    pop_stats = insight.popularity_stats(rec_tops)

    pace_map = insight.realized_pace_table(db_path)
    by_horse = {k: v[["race_id", "date", "finish"]]
                for k, v in preds.groupby("horse_id", sort=False)}
    ctx = {"pace_map": pace_map, "horse_hist": by_horse.get}

    paymap = load_fukusho_payouts(db_path)

    with sqlite3.connect(db_path) as conn:
        races_master = pd.read_sql(
            "SELECT race_id, post_time, grade FROM races", conn
        ).set_index("race_id")
        # 荒れ度の答え合わせ用 (1レースに複数組ある場合は最高額)
        trifecta = pd.read_sql(
            "SELECT race_id, MAX(pay) AS pay FROM payouts "
            "WHERE bet_type='三連単' GROUP BY race_id", conn
        ).set_index("race_id")["pay"]

    race_rows, ai_raw = [], []
    for race_id, g in preds.groupby("race_id", sort=False):
        top = g.sort_values("rank").iloc[0]
        extra = races_master.loc[race_id] if race_id in races_master.index else None
        date = pd.Timestamp(top["date"]).strftime("%Y-%m-%d")
        info = {
            "date": date,
            "place": top["place"],
            "race_no": int(top["race_no"]) if pd.notna(top["race_no"]) else None,
            "race_name": top["race_name"],
            "surface": top["surface"],
            "distance": int(top["distance"]) if pd.notna(top["distance"]) else None,
            "going": top["going"],
            "weather": top["weather"],
            "is_nar": int(top["is_nar"]) if pd.notna(top["is_nar"]) else 0,
            "post_time": None if extra is None else extra["post_time"],
            "grade": None if extra is None else extra["grade"],
        }
        extras, extra_cols = enrich(g, pd.Timestamp(date), ctx)
        ai_raw.append(_ai_upset(g["p_win_ability"]))
        race_rows.append(race_row(
            race_id, g, info,
            fuku_pay=paymap.get((race_id, int(top["horse_no"]))),
            trifecta_pay=(int(trifecta.loc[race_id])
                          if race_id in trifecta.index else None),
            in_sample=bool(split_date) and date <= split_date,
            pop_stats=pop_stats, extras=extras, extra_cols=extra_cols,
        ))

    races = pd.DataFrame(race_rows)

    # 荒れ度の較正を実配当から学習し直し、推定をその場で差し替える
    upset_calib = _fit_upset_calib(races, ai_raw, progress)
    for t, col in ((100_000, "p_hit_100k"), (1_000_000, "p_hit_1m")):
        ab = upset_calib["market"].get(str(t))
        if ab is None:
            continue
        races[col] = [None if pd.isna(v)
                      else upset_mod.calibrate(float(v), t, {str(t): ab})
                      for v in races[f"_raw_{t}"]]
    races["upset_label"] = [upset_label(v) for v in races["p_hit_1m"]]
    upset_report = _upset_check(races, ai_raw, upset_calib, split_date, progress)
    races = races[[c for c in races.columns if not c.startswith("_")]]

    # 100万馬券の法則 (scripts/million_rules.py で見つけたもの) に当てはまるか
    mmeta = insight.load_meta(db_path, "million_rules", {})
    mm = million.annotate(preds.assign(grade=preds["race_id"].map(races_master["grade"])),
                          mmeta, million.load_wins(db_path))
    races["million_n"] = races["race_id"].map(mm["million_n"]) if len(mm) else None
    races["million_rules"] = races["race_id"].map(mm["million_rules"]) if len(mm) else None
    if len(mm):
        progress(f"100万馬券の法則に当てはまるレース: {int((mm['million_n'] > 0).sum())}件")
    _pace_check(races, pace_map, progress)

    rows = preds[PRED_COLS].replace({np.nan: None})

    # 収集バッチが書き込み中だと SQLite がロックされるので、少し待つ
    conn = sqlite3.connect(db_path, timeout=120)
    try:
        insight.save_meta(conn, "index_table", table)
        insight.save_meta(conn, "pop_stats", pop_stats)
        insight.save_meta(conn, "upset_calib", upset_calib)
        insight.save_meta(conn, "upset_report", upset_report)
        insight.save_meta(conn, "comp_tables", comp_tables)
        insight.save_meta(conn, "index_blend", index_blend)
        insight.save_meta(conn, "index_report", report)
        conn.commit()
        conn.executescript(SCHEMA)
        races.to_sql("site_races_new", conn, if_exists="append", index=False)
        rows.to_sql("site_preds_new", conn, if_exists="append", index=False)
        conn.commit()
        conn.executescript(SWAP)
        conn.commit()
    finally:
        conn.close()

    n_rec = int(races["is_rec"].sum())
    progress(
        f"site_races {len(races)}件 / site_preds {len(rows)}件 を書き込みました"
        f" (推奨 {n_rec}件 / 検証期間は {split_date} 以降)。"
    )
    return {"races": len(races), "preds": len(rows), "rec": n_rec,
            "split_date": split_date}


def main():
    ap = argparse.ArgumentParser(description="サイト表示用データの事前計算")
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD 以降だけ計算")
    args = ap.parse_args()
    build(date_from=args.date_from)


if __name__ == "__main__":
    main()
