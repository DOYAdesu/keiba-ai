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
from keiba.upset import payout_probs

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
    trifecta_pay   INTEGER
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
    out["fair_odds"] = 1.0 / out["p_win"].replace(0, np.nan)

    out["fuku_odds"] = predict_fukusho_odds(bundle.get("fuku_odds_model"), out)
    out["ev_fuku"] = fukusho_ev(out["p_top3"], out["fuku_odds"])

    out = out.sort_values(["race_id", "p_win"], ascending=[True, False])
    out["rank"] = out.groupby("race_id").cumcount() + 1
    out["mark"] = [MARKS[r - 1] if r <= len(MARKS) else "" for r in out["rank"]]
    return out, str(bundle.get("split_date") or "")


def _reason(top, second, field_size):
    """スクショ同様の「根拠」テキストを組み立てる。"""
    parts = [
        f"モデル勝率{top['p_win'] * 100:.0f}%・複勝率{top['p_top3'] * 100:.0f}%の"
        + ("抜けた本命" if (top["p_top3"] - second) >= REC_MIN_GAP else "本命")
    ]
    if top["p_top3"] >= 0.75:
        parts[0] += "（複勝向き）"
    if pd.notna(top.get("ev_fuku")) and top["ev_fuku"] >= STAR_MIN_EV:
        parts.append(
            f"★複勝期待値{top['ev_fuku']:.2f}"
            f"（予測複勝{top['fuku_odds']:.1f}倍）"
        )
    pop = top.get("popularity")
    if pd.notna(pop):
        pop = int(pop)
        if pop == 1:
            parts.append("1番人気")
        elif pop <= 3:
            parts.append(f"{pop}番人気（1番人気でない堅軸）")
        else:
            parts.append(f"{pop}番人気の穴狙い")
    if pd.notna(field_size):
        if field_size <= 10:
            parts.append(f"少頭数{int(field_size)}頭")
    return " ／ ".join(parts)


def race_row(race_id, g, info, fuku_pay=None, trifecta_pay=None, in_sample=0):
    """1レースぶんの site_races 行を作る。

    g: そのレースの全頭 (rank 昇順)。info: 日付・場・距離などのレース情報。
    終わったレースにも、これからのレースにも同じ形で使う。
    """
    g = g.sort_values("rank")
    top = g.iloc[0]
    second_top3 = float(g.iloc[1]["p_top3"]) if len(g) > 1 else 0.0
    gap = float(top["p_top3"]) - second_top3
    upset = payout_probs(g["p_win"], g["odds"])
    finish = top.get("finish")
    return {
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
        "upset_label": upset_label(upset[1_000_000]),
        "reason": _reason(top, second_top3, len(g)),
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
    }


# site_preds に入れる列 (upcoming 側と揃える)
PRED_COLS = ["race_id", "rank", "mark", "horse_no", "waku", "horse_name",
             "sire", "dam_sire", "sex", "age",
             "kinryo", "jockey_name", "trainer_name",
             "weight", "weight_diff", "odds", "popularity",
             "p_win", "p_top3", "fair_odds", "fuku_odds", "ev_fuku", "finish",
             "time_sec", "agari", "corner1"]


def build(db_path=DB_PATH, model_path=MODEL_PATH, date_from=None, progress=print):
    preds, split_date = score_all(db_path, model_path, date_from)
    progress(f"{preds['race_id'].nunique()}レース / {len(preds)}頭を推論しました。")

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

    race_rows = []
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
        race_rows.append(race_row(
            race_id, g, info,
            fuku_pay=paymap.get((race_id, int(top["horse_no"]))),
            trifecta_pay=(int(trifecta.loc[race_id])
                          if race_id in trifecta.index else None),
            in_sample=bool(split_date) and date <= split_date,
        ))

    races = pd.DataFrame(race_rows)

    rows = preds[PRED_COLS].replace({np.nan: None})

    # 収集バッチが書き込み中だと SQLite がロックされるので、少し待つ
    conn = sqlite3.connect(db_path, timeout=120)
    try:
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
