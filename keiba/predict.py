"""出馬表(netkeiba shutuba ページ)を取得し、学習済みモデルで予想・買い目提案する。"""
import argparse
import datetime as dt
import re
import time

import joblib
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from .bets import MARKS, normalize_win_probs, suggest_bets
from .features import (
    FEATURES, GOING_MAP, SEX_MAP, SURFACE_MAP,
    add_market_features, aptitude_stats, horse_stats, jockey_stats,
    prepare_history, sire_stats,
)
from .scraper import HEADERS, NAR_PLACES, PLACES, WAIT_SEC, DB_PATH
from .train import MODEL_PATH

SHUTUBA_URL_JRA = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
SHUTUBA_URL_NAR = "https://nar.netkeiba.com/race/shutuba.html?race_id={race_id}"


def fetch_shutuba(race_id):
    """出馬表を取得して (レース情報dict, 出走馬DataFrame) を返す。"""
    is_nar = race_id[4:6] in NAR_PLACES
    url = (SHUTUBA_URL_NAR if is_nar else SHUTUBA_URL_JRA).format(race_id=race_id)
    resp = requests.get(url, headers=HEADERS, timeout=20)
    time.sleep(WAIT_SEC)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    soup = BeautifulSoup(resp.text, "lxml")

    table = soup.find("table", class_=re.compile("Shutuba_?Table"))
    if table is None:
        raise RuntimeError(f"出馬表が見つかりません: {url}")

    name_el = soup.find(class_="RaceName")
    race_name = name_el.get_text(strip=True) if name_el else ""
    data1 = soup.find(class_="RaceData01")
    data1_text = data1.get_text(" ", strip=True) if data1 else ""
    m = re.search(r"([芝ダ障])\D*?(\d{3,4})m", data1_text)
    surface = m.group(1) if m else None
    distance = int(m.group(2)) if m else None
    m = re.search(r"馬場\s*:\s*(\S+)", data1_text)
    going = m.group(1) if m else None

    info = {
        "race_id": race_id,
        "race_name": race_name,
        "place": PLACES.get(race_id[4:6], race_id[4:6]),
        "race_no": int(race_id[-2:]),
        "surface": surface,
        "distance": distance,
        "going": going,
        "is_nar": 1 if is_nar else 0,
        "url": url,
    }

    entries = []
    for tr in table.find_all("tr", class_=re.compile("HorseList")):
        tds = tr.find_all("td")
        if not tds:
            continue
        texts = [td.get_text(strip=True) for td in tds]
        row_text = " ".join(texts)

        e = {}
        nums = [t for t in texts[:3] if t.isdigit()]
        e["waku"] = int(nums[0]) if len(nums) >= 1 else None
        e["horse_no"] = int(nums[1]) if len(nums) >= 2 else None

        a = tr.find("a", href=re.compile(r"/horse/"))
        e["horse_name"] = a.get_text(strip=True) if a else ""
        m = re.search(r"/horse/(\w+)", a["href"]) if a else None
        e["horse_id"] = m.group(1) if m else None

        a = tr.find("a", href=re.compile(r"/jockey/"))
        e["jockey_name"] = a.get_text(strip=True) if a else ""
        m = re.search(r"/jockey/(?:result/recent/)?(\w+)", a["href"]) if a else None
        e["jockey_id"] = m.group(1) if m else None

        m = re.search(r"(牡|牝|セ|せん|騙)(\d+)", row_text)
        e["sex"] = m.group(1) if m else None
        e["age"] = int(m.group(2)) if m else None

        kinryo = None
        for t in texts:
            m = re.fullmatch(r"(\d{2}(?:\.\d)?)", t)
            if m and 45 <= float(m.group(1)) <= 65:
                kinryo = float(m.group(1))
                break
        e["kinryo"] = kinryo

        m = re.search(r"(\d{3})\(([-+]?\d+)\)", row_text)
        e["weight"] = float(m.group(1)) if m else None
        e["weight_diff"] = float(m.group(2)) if m else None

        # 単勝オッズ (静的HTMLに入っている場合のみ。JS描画のページでは取れない)
        odds = None
        for td in tds:
            cls = " ".join(td.get("class") or [])
            if "Odds" in cls or "Popular" in cls:
                m = re.search(r"(\d{1,4}\.\d)", td.get_text(strip=True))
                if m:
                    odds = float(m.group(1))
                    break
        e["odds"] = odds

        if e["horse_id"]:
            entries.append(e)

    if not entries:
        raise RuntimeError(f"出走馬を解析できませんでした: {url}")
    return info, pd.DataFrame(entries)


def build_features_for_entries(info, entries, hist, horses, as_of):
    from .course_bias import course_scores, fit_scores
    from .course_geo import geo_features

    place_code_str = info["race_id"][4:6]
    turn, straight_len, slope_level, turf_yoshiba = geo_features(
        place_code_str, info["surface"], info["distance"]
    )
    inner, front = course_scores(place_code_str, info["surface"], info["distance"])

    rows = []
    for _, e in entries.iterrows():
        f = {
            "waku": e["waku"],
            "horse_no": e["horse_no"],
            "field_size": len(entries),
            "age": e["age"],
            "sex_code": SEX_MAP.get(e["sex"]),
            "kinryo": e["kinryo"],
            "weight": e["weight"],
            "weight_diff": e["weight_diff"],
            "distance": info["distance"],
            "surface_code": SURFACE_MAP.get(info["surface"]),
            "going_code": GOING_MAP.get(info["going"]),
            "place_code": int(info["race_id"][4:6]),
            "is_nar": info["is_nar"],
        }
        f.update(horse_stats(hist, e["horse_id"], as_of))
        f.update(jockey_stats(hist, e["jockey_id"], as_of))
        f.update(sire_stats(hist, horses, e["horse_id"], as_of))
        f.update(aptitude_stats(
            hist, e["horse_id"], as_of, place_code_str, turn, info["distance"]
        ))
        f.update(hist.workout_stats(e["horse_id"], as_of))

        f["course_inner_adv"], f["course_front_adv"] = inner, front
        f["waku_fit"], f["style_fit"] = fit_scores(
            inner, front, e["waku"], f.get("avg_corner1_ratio5")
        )
        f["turn"] = turn
        f["straight_len"] = straight_len
        f["slope_level"] = slope_level
        f["turf_yoshiba"] = turf_yoshiba
        f["form_trend"] = (
            f["last_finish"] - f["avg_finish5"]
            if f.get("last_finish") is not None else np.nan
        )
        rows.append(f)

    X = pd.DataFrame(rows)
    # ペース圧: 逃げ・先行候補 (先行度<0.3) の数 (レース全体で共通)
    n_front = int((X["avg_corner1_ratio5"] < 0.3).sum())
    X["front_count"] = n_front
    X["pace_pressure_self"] = n_front * (0.5 - X["avg_corner1_ratio5"])
    return X[FEATURES].astype(float)


def save_prediction(info, out, as_of, db_path=DB_PATH):
    from .scraper import connect

    conn = connect(db_path)
    try:
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        for _, r in out.iterrows():
            conn.execute(
                "INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (info["race_id"], int(r["horse_no"]), r["horse_name"], r["印"],
                 int(r["予想順位"]), float(r["p_top3"]), float(r["p_win_norm"]),
                 str(pd.Timestamp(as_of).date()), info["place"], info["race_no"],
                 info["race_name"], now),
            )
        conn.commit()
    finally:
        conn.close()


def load_context(db_path=DB_PATH, model_path=MODEL_PATH):
    """モデルと過去成績をまとめて読み込む (複数レースを予想するとき使い回す)。"""
    bundle = joblib.load(model_path)
    if "model_top3" not in bundle:
        raise RuntimeError("旧形式のモデルです。「学習」を実行し直してください。")
    hist, horses = prepare_history(db_path)
    return {"bundle": bundle, "hist": hist, "horses": horses}


def predict_race(race_id, as_of=None, db_path=DB_PATH, model_path=MODEL_PATH,
                 context=None, refresh_odds=False):
    """出馬表を取得して予想する。

    返り値: (レース情報, 予想DataFrame, 買い目DataFrame, 特徴量X, SHAP寄与行列)
    予想DataFrame の entry_idx 列が X / 寄与行列の行番号に対応する。
    """
    from .explain import contributions

    ctx = context or load_context(db_path, model_path)
    bundle = ctx["bundle"]
    model_top3 = bundle["model_top3"]
    model_win = bundle["model_win"]

    as_of = pd.Timestamp(as_of or dt.date.today())
    is_nar = race_id[4:6] in NAR_PLACES

    # 中央は JRA-VAN の出馬表を優先 (公式・オッズ付き・ブロックの心配なし)
    info, entries = None, None
    prefetched = (context or {}).get("entries", {}).get(race_id) if context else None
    if not is_nar:
        if prefetched is not None:
            jv_info, jv_entries = prefetched
        else:
            from .jvlink import fetch_jv_entries

            jv_info, jv_entries = fetch_jv_entries(race_id, as_of.date())
        if not jv_entries.empty:
            entries = jv_entries
            info = {
                "race_id": race_id,
                "race_name": (jv_info or {}).get("race_name", ""),
                "place": PLACES.get(race_id[4:6], race_id[4:6]),
                "race_no": int(race_id[-2:]),
                "surface": (jv_info or {}).get("surface") or None,
                "distance": int((jv_info or {}).get("distance") or 0) or None,
                "going": (jv_info or {}).get("going") or None,
                "is_nar": 0,
                "url": "JRA-VAN",
            }
    if entries is None:
        info, entries = fetch_shutuba(race_id)

    # 取得元によって型がまちまちなので、数値列をここで揃える
    entries = entries.copy()
    for c in ("waku", "horse_no", "age"):
        if c in entries.columns:
            entries[c] = pd.to_numeric(entries[c], errors="coerce")
    for c in ("kinryo", "weight", "weight_diff", "odds"):
        if c in entries.columns:
            entries[c] = pd.to_numeric(entries[c], errors="coerce")
        else:
            entries[c] = np.nan

    hist, horses = ctx["hist"], ctx["horses"]

    # オッズが無い (または明示的に更新指定) なら JRA-VAN 速報から取り直す
    if info["is_nar"] == 0 and (refresh_odds or entries["odds"].isna().all()):
        from .jvlink import fetch_jv_odds

        jv_odds = fetch_jv_odds(race_id, as_of.date())
        if jv_odds:
            fresh = entries["horse_no"].map(jv_odds)
            entries["odds"] = fresh if refresh_odds else entries["odds"].fillna(fresh)

    cals = bundle.get("calibrators", {})

    def calibrate(key, prob):
        ir = cals.get(key)
        return np.clip(ir.predict(prob), 0.0, 1.0) if ir is not None else prob

    X = build_features_for_entries(info, entries, hist, horses, as_of)
    p_top3 = calibrate("top3", model_top3.predict_proba(X)[:, 1])
    p_win_norm = normalize_win_probs(
        calibrate("win", model_win.predict_proba(X)[:, 1])
    )
    contribs = contributions(model_top3, X)

    # 市場モデル (オッズあり) が使えるなら的中率重視の確率も出す
    market_ok = (
        "model_market_top3" in bundle
        and entries["odds"].notna().sum() >= max(3, len(entries) // 2)
    )
    if market_ok:
        Xm = add_market_features(X, entries["odds"].values, [race_id] * len(entries))
        m_top3 = calibrate(
            "market_top3", bundle["model_market_top3"].predict_proba(Xm)[:, 1]
        )
        m_win = normalize_win_probs(calibrate(
            "market_win", bundle["model_market_win"].predict_proba(Xm)[:, 1]
        ))
    else:
        m_top3 = m_win = None
    info["market_used"] = bool(market_ok)

    # 狙い目パターン判定 (コース攻略表)
    from .course_bias import match_patterns

    pat_df = pd.DataFrame({
        "waku": X["waku"],
        "corner_ratio": X["avg_corner1_ratio5"],
        "agari_rank": X["avg_agari5"].rank(method="min"),
    })
    pattern_flags, pattern_name = match_patterns(
        race_id[4:6], info["surface"], info["distance"], pat_df
    )
    info["pattern_name"] = pattern_name

    out = entries[["horse_no", "waku", "horse_name", "sex", "age",
                   "kinryo", "jockey_name", "weight", "odds"]].copy()
    out["entry_idx"] = range(len(out))
    out["狙い目"] = ["★" if f else "" for f in pattern_flags]
    out["p_top3"] = np.round(p_top3, 3)
    out["p_win_norm"] = np.round(p_win_norm, 3)
    out["過去出走数"] = X["career_starts"].astype(int).values
    if m_top3 is not None:
        # 市場モデルの確率。印はこちら (的中率が高い) を基準に付ける
        out["m_top3"] = np.round(m_top3, 3)
        out["m_win"] = np.round(m_win, 3)
        # 妙味: 能力モデルが市場より高く評価している度合い
        out["妙味"] = np.round(p_win_norm - m_win, 3)
        out = out.sort_values("m_top3", ascending=False).reset_index(drop=True)
    else:
        out = out.sort_values("p_top3", ascending=False).reset_index(drop=True)
    out.insert(0, "印", [MARKS[i] if i < len(MARKS) else "" for i in range(len(out))])
    out.insert(0, "予想順位", range(1, len(out) + 1))
    out.insert(2, "狙い目", out.pop("狙い目"))
    # 買い目計算は印の基準と揃える (市場モデルがあればそちらの勝率を使う)
    if "m_win" in out.columns:
        out["p_win_for_bets"] = out["m_win"]
    else:
        out["p_win_for_bets"] = out["p_win_norm"]
    # オッズが取れた場合は単勝期待値 (予測勝率×オッズ) を付ける
    if out["odds"].notna().any():
        out["単勝EV"] = np.round(out["p_win_norm"] * out["odds"], 2)
    else:
        out = out.drop(columns=["odds"])

    # 複勝の期待値 (妙味): 予測複勝オッズ × 複勝圏確率
    from .ev import DEFAULT_EV_THRESHOLD, fukusho_ev, predict_fukusho_odds

    fom = bundle.get("fuku_odds_model")
    if fom is not None and "odds" in out.columns and out["odds"].notna().any():
        frame = pd.DataFrame({
            "odds": out["odds"],
            "popularity": out["odds"].rank(method="min"),
            "field_size": len(out),
        })
        fuku_odds = predict_fukusho_odds(fom, frame)
        prob_col = "m_top3" if "m_top3" in out.columns else "p_top3"
        out["複勝EV"] = np.round(fukusho_ev(out[prob_col], fuku_odds), 2)
        out["EV買い"] = np.where(out["複勝EV"] >= DEFAULT_EV_THRESHOLD, "◎買", "")
        info["ev_threshold"] = DEFAULT_EV_THRESHOLD

    bets = suggest_bets(out)
    save_prediction(info, out, as_of, db_path)
    return info, out, bets, X, contribs


def main():
    ap = argparse.ArgumentParser(description="レース予想")
    ap.add_argument("race_id", help="netkeiba の race_id (12桁)")
    ap.add_argument("--date", help="レース日 YYYY-MM-DD (省略時は今日)")
    args = ap.parse_args()

    info, out, bets, X, contribs = predict_race(args.race_id, as_of=args.date)
    print(f"{info['place']}{info['race_no']}R {info['race_name']} "
          f"{info['surface'] or ''}{info['distance'] or ''}m")
    if info.get("pattern_name"):
        print(f"⭐ 狙い目パターン対象コース: {info['pattern_name']} (該当馬に★)")
    show = out.rename(columns={"p_top3": "複勝圏確率", "p_win_norm": "予測勝率"})
    print(show.drop(columns=["entry_idx"]).to_string(index=False))
    print("\n=== 買い目提案 ===")
    print(bets.to_string(index=False))

    from .explain import explain_table, summary_text

    print("\n=== 予想根拠 (上位3頭) ===")
    for i in range(min(3, len(out))):
        r = out.iloc[i]
        exp = explain_table(X, contribs, int(r["entry_idx"]))
        text = summary_text(exp, r["horse_name"]).replace("**", "")
        print(f"\n{r['印']} {r['horse_no']} {r['horse_name']}: {text}")


if __name__ == "__main__":
    main()
