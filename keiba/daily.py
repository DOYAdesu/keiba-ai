"""開催日の一括予想と「勝負レース」抽出。

全レースを予想し、モデルの確信度が高い(=荒れにくい)レースを推奨する。
確信度は本命の複勝圏確率と、本命と2番手の差 (抜けている度合い) で測る。
"""
import argparse
import datetime as dt

import pandas as pd

from .jvlink import fetch_jv_entries_many, list_day_races
from .predict import load_context, predict_race
from .scraper import DB_PATH
from .train import MODEL_PATH


def confidence(out):
    """予想結果から確信度スコアを計算する。0〜1程度。"""
    col = "m_top3" if "m_top3" in out.columns else "p_top3"
    s = out[col].astype(float)
    top = float(s.iloc[0])
    gap = float(s.iloc[0] - s.iloc[1]) if len(s) > 1 else 0.0
    return round(top * 0.7 + gap * 1.5, 3)


def predict_day(date, min_confidence=0.5, db_path=DB_PATH, model_path=MODEL_PATH,
                progress=None):
    """指定日の全レースを予想するジェネレータ。

    yield: ("progress", メッセージ) または ("done", DataFrame)
    """
    races = list_day_races(date, db_path)
    if races.empty:
        yield ("progress", f"{date} のレース一覧がありません。"
                           "「データ収集」でJRA-VANから取得してください。")
        yield ("done", pd.DataFrame())
        return

    total = len(races)
    yield ("progress", f"{total}レースの出馬表とモデルを読み込み中...")
    ctx = load_context(db_path, model_path)
    ctx["entries"] = fetch_jv_entries_many(races["race_id"].tolist(), date)

    n_with_entries = sum(
        1 for _, df in ctx["entries"].values() if df is not None and not df.empty
    )
    if n_with_entries == 0:
        yield ("progress",
               "出馬表が1件も見つかりません。「データ収集」タブで JRA-VAN から"
               "成績を取得してください (利用キーが未設定だと取得できません)。")
        yield ("done", pd.DataFrame())
        return

    rows = []
    errors = []
    for i, (_, r) in enumerate(races.iterrows(), 1):
        label = f"{r['place']}{r['race_no']}R {r['race_name']}"
        yield ("progress", f"[{i}/{total}] {label} を予想中...")
        try:
            info, out, bets, X, contribs = predict_race(
                r["race_id"], as_of=date, db_path=db_path, model_path=model_path,
                context=ctx,
            )
        except Exception as e:
            errors.append(f"{label}: {e}")
            yield ("progress", f"[{i}/{total}] {label} スキップ ({e})")
            continue
        top = out.iloc[0]
        conf = confidence(out)
        prob_col = "m_top3" if "m_top3" in out.columns else "p_top3"
        win_col = "m_win" if "m_win" in out.columns else "p_win_norm"
        rows.append({
            "勝負": "🔥" if conf >= min_confidence else "",
            "確信度": conf,
            "発走": r.get("post_time") or "",
            "場": r["place"],
            "R": int(r["race_no"]),
            "レース名": r["race_name"],
            "◎": f"{int(top['horse_no'])} {top['horse_name']}",
            "複勝圏確率": float(top[prob_col]),
            "予測勝率": float(top[win_col]),
            "オッズ": float(top["odds"]) if "odds" in out.columns
                      and pd.notna(top.get("odds")) else None,
            "race_id": r["race_id"],
        })

    if not rows:
        detail = ("\n".join(errors[:3]) + ("\n..." if len(errors) > 3 else "")) \
            if errors else ""
        yield ("progress", "予想できたレースがありませんでした。\n" + detail)
        yield ("done", pd.DataFrame())
        return

    df = pd.DataFrame(rows).sort_values("確信度", ascending=False).reset_index(drop=True)
    yield ("done", df)


def main():
    ap = argparse.ArgumentParser(description="開催日の一括予想")
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD (省略時は今日)")
    ap.add_argument("--min-confidence", type=float, default=0.5)
    args = ap.parse_args()
    date = args.date or str(dt.date.today())

    result = None
    for kind, payload in predict_day(date, args.min_confidence):
        if kind == "progress":
            print(payload, flush=True)
        else:
            result = payload
    if result is None or result.empty:
        print("予想できるレースがありませんでした。")
        return
    print()
    print(f"=== {date} 予想一覧 (確信度順) ===")
    print(result.drop(columns=["race_id"]).to_string(index=False))
    hot = result[result["勝負"] == "🔥"]
    print()
    print(f"勝負レース: {len(hot)}件")


if __name__ == "__main__":
    main()
