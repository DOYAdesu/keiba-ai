"""競馬予想アプリ (Streamlit UI)

起動: streamlit run app.py
"""
import datetime as dt
import sqlite3

import pandas as pd
import streamlit as st

from keiba.scraper import (
    DB_PATH, JRA_PLACES, NAR_PLACES, BlockedError,
    collect_jra, collect_nar, collect_pedigree, refresh_v1_races,
)
from keiba.jvlink import (
    collect_jv_horse, collect_jv_race, collect_jv_workout,
    find_python32, open_settings,
)
from keiba.train import MODEL_PATH, train
from keiba.predict import predict_race
from keiba.backtest import backtest
from keiba.explain import explain_table, summary_text
from keiba.racelist import find_jra_races, nar_race_ids
from keiba.record import check_result, honmei_stats, list_predictions
from keiba.betsim import simulate as betsim_simulate
from keiba.daysim import (
    EV_STRATEGY, STRATEGIES as DAY_STRATEGIES, simulate_days,
)
from keiba.daily import predict_day
from keiba.jvlink import list_day_races
from keiba import ledger

st.set_page_config(page_title="競馬予想AI", page_icon="🏇", layout="wide")
st.title("🏇 競馬予想AI (中央+地方)")


def run_generator(gen):
    """収集ジェネレータを回しつつログを表示する。正常完了で True。"""
    log_box = st.empty()
    lines = []
    try:
        with st.spinner("実行中... (ブラウザを閉じないでください)"):
            for msg in gen:
                lines.append(msg)
                log_box.code("\n".join(lines[-20:]))
    except BlockedError as e:
        st.error(str(e))
        return False
    return True


# --- サイドバー: DB の状態 ---
with st.sidebar:
    st.header("データベース状況")
    if DB_PATH.exists():
        with sqlite3.connect(DB_PATH) as conn:
            n_races = conn.execute(
                "SELECT COUNT(DISTINCT race_id) FROM results"
            ).fetchone()[0]
            n_rows = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
            period = conn.execute(
                "SELECT MIN(date), MAX(date) FROM results"
            ).fetchone()
            try:
                n_ped = conn.execute("SELECT COUNT(*) FROM horses").fetchone()[0]
                n_horses = conn.execute(
                    "SELECT COUNT(DISTINCT horse_id) FROM results"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                n_ped, n_horses = 0, 0
        st.metric("収集済みレース数", f"{n_races:,}")
        st.metric("出走データ行数", f"{n_rows:,}")
        st.metric("血統取得済み", f"{n_ped:,} / {n_horses:,}頭")
        if period[0]:
            st.caption(f"期間: {period[0]} 〜 {period[1]}")
    else:
        st.info("まだデータがありません。「データ収集」タブから始めてください。")
    st.divider()
    st.caption("モデル: " + ("学習済み ✅" if MODEL_PATH.exists() else "未学習"))

(tab_today, tab_predict, tab_record, tab_ledger,
 tab_backtest, tab_train, tab_collect) = st.tabs(
    ["🔥 今日の予想", "🔮 レース予想", "📒 予想履歴", "💴 収支管理",
     "📈 回収率検証", "🧠 学習", "📥 データ収集"]
)

# --- 今日の予想 (一括予想・勝負レース) ---
with tab_today:
    st.subheader("開催日の一括予想と勝負レース")
    st.caption(
        "その日の中央全レースをまとめて予想し、モデルの確信度が高い順に並べます。"
        "確信度は本命の複勝圏確率と2番手との差から計算します。"
    )
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        day = st.date_input("開催日", dt.date.today(), key="today_date")
    with c2:
        min_conf = st.slider("勝負レースの閾値", 0.3, 0.9, 0.5, 0.05)
    with c3:
        st.write("")
        run_day = st.button("一括予想を実行", type="primary")

    races_today = list_day_races(str(day))
    if races_today.empty:
        st.info(
            f"{day} のレース情報がありません。"
            "「データ収集」タブで JRA-VAN から成績を取得すると、"
            "出馬表とレース一覧が保存されます。"
        )
    else:
        st.caption(f"{len(races_today)}レースが登録されています。")

    if run_day:
        box = st.empty()
        result, last_msg = None, ""
        for kind, payload in predict_day(str(day), min_conf):
            if kind == "progress":
                last_msg = payload
                box.info(payload)
            else:
                result = payload
        box.empty()
        if result is None or result.empty:
            st.session_state.pop("day_result", None)
            st.warning(last_msg or "予想できるレースがありませんでした。")
        else:
            st.session_state["day_result"] = result

    if "day_result" in st.session_state:
        res = st.session_state["day_result"]
        hot = res[res["勝負"] == "🔥"]
        st.metric("勝負レース", f"{len(hot)} / {len(res)}")
        st.dataframe(
            res.drop(columns=["race_id"]),
            hide_index=True, use_container_width=True,
            column_config={
                "複勝圏確率": st.column_config.ProgressColumn(
                    "複勝圏確率", min_value=0.0, max_value=1.0, format="%.3f"),
                "予測勝率": st.column_config.ProgressColumn(
                    "予測勝率", min_value=0.0, max_value=1.0, format="%.3f"),
            },
        )
        st.caption(
            "🔥 = 確信度が閾値以上のレース。買うレースを絞ることが的中率を上げる"
            "一番確実な方法です。個別の根拠は「レース予想」タブで race_id を指定してください。"
        )

# --- 収支管理 ---
with tab_ledger:
    st.subheader("収支管理")
    st.caption("実際に購入した馬券を記録すると、結果確定後に払戻を自動計算します。")

    with st.expander("馬券を記録する", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            t_race_id = st.text_input("race_id (12桁)", max_chars=12, key="t_rid")
        with c2:
            t_type = st.selectbox("券種", ledger.BET_TYPES)
        with c3:
            t_combo = st.text_input("買い目 (例: 5 / 3-8 / 2-5-9)", key="t_combo")
        with c4:
            t_amount = st.number_input("金額(円)", 100, 1000000, 100, step=100)
        if st.button("記録する"):
            if len(t_race_id) == 12 and t_race_id.isdigit() and t_combo:
                ledger.add_ticket(t_race_id, t_type, t_combo, t_amount)
                st.success("記録しました。")
            else:
                st.error("race_id (12桁) と買い目を入力してください。")

    if st.button("結果を反映する (払戻を確定)"):
        n = ledger.settle()
        st.success(f"{n}点の払戻を確定しました。")

    s = ledger.summary()
    if s is None:
        st.info("まだ記録がありません。")
    else:
        t = s["total"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("投資額", f"{t['投資額']:,}円")
        c2.metric("払戻額", f"{t['払戻額']:,}円")
        c3.metric("収支", f"{t['収支']:+,}円")
        c4.metric("回収率", f"{t['回収率']:.1%}" if t["回収率"] else "-")
        if not s["by_type"].empty:
            st.markdown("**券種別**")
            bt = s["by_type"].copy()
            bt["回収率"] = bt["回収率"].map(lambda v: f"{v:.1%}")
            st.dataframe(bt, hide_index=True, use_container_width=True)
        if not s["by_month"].empty:
            st.markdown("**月別**")
            bm = s["by_month"].copy()
            bm["回収率"] = bm["回収率"].map(lambda v: f"{v:.1%}")
            st.dataframe(bm, hide_index=True, use_container_width=True)
        st.markdown("**購入履歴**")
        st.dataframe(ledger.list_tickets(), hide_index=True, use_container_width=True)

# --- データ収集 ---
with tab_collect:
    st.subheader("レース結果の収集 (netkeiba)")
    st.caption("1秒/リクエストで丁寧に取得します。1年分の中央で1〜2時間程度かかります。")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**中央競馬 (JRA)**")
        this_year = dt.date.today().year
        y_from = st.number_input("開始年", 2010, this_year, this_year - 1)
        y_to = st.number_input("終了年", 2010, this_year, this_year)
        jra_places = st.multiselect(
            "競馬場 (未選択=全場)",
            options=list(JRA_PLACES),
            format_func=lambda c: f"{c} {JRA_PLACES[c]}",
        )
        run_jra = st.button("中央を収集開始", type="primary")
    with col2:
        st.markdown("**地方競馬 (NAR)**")
        d_from = st.date_input("開始日", dt.date.today() - dt.timedelta(days=365))
        d_to = st.date_input("終了日", dt.date.today())
        nar_places = st.multiselect(
            "競馬場 (未選択=全場)",
            options=list(NAR_PLACES),
            format_func=lambda c: f"{c} {NAR_PLACES[c]}",
        )
        run_nar = st.button("地方を収集開始", type="primary")

    st.divider()
    st.markdown("**追加データ**")
    c1, c2 = st.columns(2)
    with c1:
        run_ped = st.button("血統を収集 (父・母父)")
        st.caption("血統特徴量に必要。未取得の馬だけ取得します (1頭=1秒)。")
    with c2:
        run_refresh = st.button("旧データの通過・上りを補完")
        st.caption("拡張前に収集したレースの脚質データを再取得します。")

    st.divider()
    st.markdown("**JRA-VAN Data Lab (公式データ・中央のみ)**")
    py32 = find_python32()
    if py32 is None:
        st.warning(
            "32bit Python が未検出です。JV-Link は 32bit 専用のため、"
            "[python.org](https://www.python.org/downloads/windows/) から "
            "**Windows installer (32-bit)** をインストールしてください。"
            "インストール後にこの画面を再読み込みすると自動検出されます。"
        )
    else:
        st.caption(
            f"32bit Python 検出済み: `{py32}`。"
            "利用には JRA-VAN Data Lab の契約 (月2,090円) と利用キーの設定が必要です。"
        )
        jc1, jc2, jc3 = st.columns([1, 1, 2])
        with jc1:
            if st.button("JV-Link 設定 (利用キー入力)"):
                with st.spinner("設定画面を開いています... (閉じると戻ります)"):
                    try:
                        open_settings()
                        st.success("設定画面を閉じました。")
                    except RuntimeError as e:
                        st.error(str(e))
        with jc2:
            jv_from = st.date_input(
                "取得開始日", dt.date.today() - dt.timedelta(days=365), key="jv_from"
            )
        with jc3:
            run_jv_race = st.button("JRA-VAN から成績を取得", type="primary")
            run_jv_horse = st.button("JRA-VAN から血統を取得")
            run_jv_workout = st.button("JRA-VAN から調教を取得")
            jv_setup = st.checkbox(
                "セットアップ取得 (1年以上前のデータ)",
                help="JV-Link のセットアップ画面が別ウィンドウで開きます。"
                     "「スタートキットを持っていない」を選ぶと全データの巨大ダウンロードが始まり、"
                     "完了まで数時間かかることがあります。通常は不要です "
                     "(チェックなしで約1年前まで取得できます)。",
            )
        st.caption(
            "通常モード (チェックなし) は約1年前まで取得でき、ダイアログも出ません。"
            "まずはチェックなしで試してください。"
        )
        if run_jv_race or run_jv_horse or run_jv_workout:
            if run_jv_race:
                gen = collect_jv_race(jv_from, setup=jv_setup)
            elif run_jv_horse:
                gen = collect_jv_horse(jv_from, setup=jv_setup)
            else:
                gen = collect_jv_workout(jv_from, setup=jv_setup)
            try:
                if run_generator(gen):
                    st.success("JRA-VAN からの取得が完了しました。")
            except RuntimeError as e:
                st.error(str(e))

    if run_jra:
        if run_generator(collect_jra(int(y_from), int(y_to), jra_places or None)):
            st.success("収集が完了しました。")
    elif run_nar:
        if run_generator(collect_nar(d_from, d_to, nar_places or None)):
            st.success("収集が完了しました。")
    elif run_ped:
        if run_generator(collect_pedigree()):
            st.success("血統収集が完了しました。")
    elif run_refresh:
        if run_generator(refresh_v1_races()):
            st.success("補完が完了しました。")

# --- 学習 ---
with tab_train:
    st.subheader("予想モデルの学習 (LightGBM)")
    st.caption("複勝圏 (3着以内) モデルと勝率 (1着) モデルの2つを学習します。")
    if st.button("学習を実行", type="primary"):
        try:
            with st.spinner("学習中..."):
                metrics = train()
            st.success("学習が完了しました。")
            if "market_top1_fukusho_rate" in metrics:
                st.markdown("**市場モデル (オッズあり・的中率重視)**")
                c1, c2, c3 = st.columns(3)
                c1.metric(
                    "複勝的中率", f"{metrics['market_top1_fukusho_rate']:.1%}",
                    delta=f"{metrics['market_top1_fukusho_rate'] - metrics['model_top1_fukusho_rate']:+.1%} vs 能力モデル",
                )
                c2.metric(
                    "単勝的中率", f"{metrics['market_top1_win_rate']:.1%}",
                    delta=f"{metrics['market_top1_win_rate'] - metrics['model_top1_win_rate']:+.1%}",
                )
                c3.metric("AUC", f"{metrics['auc_market_top3']} / {metrics['auc_market_win']}")
            else:
                st.warning(
                    "市場モデルの結果がありません。アプリを一度閉じて起動し直してから"
                    "もう一度学習すると表示されます。"
                )

            st.markdown("**能力モデル (オッズなし・妙味発見用)**")
            c1, c2, c3 = st.columns(3)
            c1.metric("複勝的中率", f"{metrics['model_top1_fukusho_rate']:.1%}")
            c2.metric("単勝的中率", f"{metrics['model_top1_win_rate']:.1%}")
            c3.metric("AUC", f"{metrics['auc_top3']} / {metrics['auc_win']}")

            if metrics["ninki1_fukusho_rate"]:
                st.caption(
                    f"参考: 1番人気の複勝率 {metrics['ninki1_fukusho_rate']:.1%} / "
                    f"勝率 {metrics['ninki1_win_rate']:.1%}"
                )
            st.caption(
                f"学習 {metrics['n_train']:,}行 / 検証 {metrics['n_valid']:,}行 "
                f"({metrics['n_races_valid']:,}レース, {metrics['valid_from']} 以降)"
            )
            st.markdown("**重要な特徴量 Top10 (複勝モデル)**")
            imp = pd.DataFrame(metrics["feature_importance"], columns=["特徴量", "重要度"])
            st.dataframe(imp, hide_index=True)
        except RuntimeError as e:
            st.error(str(e))

# --- 予想 ---
with tab_predict:
    st.subheader("レース予想 & 買い目提案")

    method = st.radio(
        "レースの選び方", ["レースを探す", "race_id を直接入力"], horizontal=True
    )
    race_id = ""
    if method == "レースを探す":
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
        with c1:
            kind = st.selectbox("中央/地方", ["地方 (NAR)", "中央 (JRA)"])
        with c2:
            target_date = st.date_input("開催日", dt.date.today(), key="find_date")
        if kind == "地方 (NAR)":
            with c3:
                pcode = st.selectbox(
                    "競馬場", list(NAR_PLACES), format_func=lambda c: NAR_PLACES[c]
                )
            with c4:
                r_no = st.selectbox("レース", list(range(1, 13)), index=10)
            race_id = nar_race_ids(target_date, pcode)[r_no - 1]
            st.caption(f"race_id: `{race_id}` (地方は日付から自動で決まります)")
        else:
            with c3:
                pcode = st.selectbox(
                    "競馬場", list(JRA_PLACES), format_func=lambda c: JRA_PLACES[c]
                )
            key = f"jra_{target_date}_{pcode}"
            if key not in st.session_state:
                if st.button("開催を検索 (数十秒かかることがあります)"):
                    box = st.empty()
                    ids = find_jra_races(
                        target_date, pcode, progress=lambda m: box.info(m)
                    )
                    box.empty()
                    st.session_state[key] = ids
                    st.rerun()
            if key in st.session_state:
                ids = st.session_state[key]
                if not ids:
                    st.warning(f"{target_date} の{JRA_PLACES[pcode]}開催は見つかりませんでした。")
                else:
                    with c4:
                        r_no = st.selectbox("レース", list(range(1, 13)), index=10)
                    race_id = ids[r_no - 1]
                    st.caption(f"race_id: `{race_id}`")
    else:
        st.markdown(
            "race_id は netkeiba の出馬表 URL に含まれる12桁の数字です "
            "(例: 中央 `202605030411` / 地方 `202644072511`)"
        )
        race_id = st.text_input("race_id (12桁)", max_chars=12)

    race_date = st.date_input("レース日 (特徴量の基準日)", dt.date.today())

    if st.button("予想する", type="primary", disabled=not race_id):
        if not MODEL_PATH.exists():
            st.error("モデルが未学習です。先に「学習」タブで学習してください。")
        elif len(race_id) != 12 or not race_id.isdigit():
            st.error("race_id は12桁の数字で入力してください。")
        else:
            try:
                with st.spinner("出馬表を取得して予想中..."):
                    info, out, bets, X, contribs = predict_race(race_id, as_of=race_date)
                st.session_state["pred"] = {
                    "info": info, "out": out, "bets": bets,
                    "X": X, "contribs": contribs,
                }
            except Exception as e:
                st.session_state.pop("pred", None)
                st.error(f"予想に失敗しました: {e}")

    if "pred" in st.session_state:
        p = st.session_state["pred"]
        info, out, bets = p["info"], p["out"], p["bets"]
        if st.button("🔄 オッズを取り直して予想を更新"):
            try:
                with st.spinner("最新オッズで再計算中..."):
                    i2, o2, b2, X2, c2 = predict_race(
                        info["race_id"], as_of=race_date, refresh_odds=True
                    )
                st.session_state["pred"] = {
                    "info": i2, "out": o2, "bets": b2, "X": X2, "contribs": c2,
                }
                st.rerun()
            except Exception as e:
                st.error(f"更新に失敗しました: {e}")
        st.markdown(
            f"### {info['place']} {info['race_no']}R {info['race_name']} "
            f"({info['surface'] or '?'}{info['distance'] or '?'}m)"
        )
        if info.get("pattern_name"):
            st.info(f"⭐ このレースは狙い目パターン「{info['pattern_name']}」の対象コースです。"
                    "該当馬には ★ が付いています。")
        if info.get("market_used"):
            st.success(
                "✅ オッズを取り込んだ**市場モデル**で予想しています "
                "(検証では複勝的中率 64.7%、能力モデル単体より約10ポイント高い)。"
                "「妙味」列がプラスの馬は、AI がオッズ以上に評価している穴候補です。"
            )
        else:
            st.info(
                "ℹ️ オッズが取得できなかったため**能力モデル**のみで予想しています。"
                "発走が近づくとオッズが出て、より的中率の高い市場モデルが使われます。"
            )
        show = out.drop(columns=["entry_idx", "p_win_for_bets"], errors="ignore").rename(
            columns={
                "p_top3": "複勝圏確率(能力)", "p_win_norm": "予測勝率(能力)",
                "m_top3": "複勝圏確率", "m_win": "予測勝率",
            }
        )
        st.caption("👇 行を選択すると、その馬の予想根拠が下に表示されます。")
        event = st.dataframe(
            show,
            hide_index=True,
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
            key="pred_table",
            column_config={
                c: st.column_config.ProgressColumn(
                    c, min_value=0.0, max_value=1.0, format="%.3f"
                )
                for c in ("複勝圏確率", "予測勝率", "複勝圏確率(能力)", "予測勝率(能力)")
                if c in show.columns
            },
        )

        # --- 選択した馬の予想根拠 ---
        if event.selection.rows:
            i = event.selection.rows[0]
            row = out.iloc[i]
            exp = explain_table(p["X"], p["contribs"], int(row["entry_idx"]))
            st.markdown(
                f"#### 🔍 {row['印'] or '─'} {row['horse_no']} {row['horse_name']} の予想根拠"
            )
            st.markdown(summary_text(exp, row["horse_name"]))
            plus = exp[exp["寄与"] > 0].head(6)
            minus = exp[exp["寄与"] < 0].sort_values("寄与").head(6)
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**📈 評価を押し上げた要因**")
                st.dataframe(plus, hide_index=True, use_container_width=True)
            with c2:
                st.markdown("**📉 評価を押し下げた要因**")
                st.dataframe(minus, hide_index=True, use_container_width=True)
            st.caption(
                "寄与はモデル内部のスコア (ログオッズ) に対する SHAP 値。"
                "絶対値が大きいほどその特徴量が予想に効いています。"
                "「データなし」は DB に履歴が無く判断材料にできなかった項目です。"
            )

        st.markdown("#### 💰 買い目提案")
        st.dataframe(bets, hide_index=True, use_container_width=True)
        st.caption(
            "推定的中率は予測勝率からの Harville 近似による理論値です。"
            "オッズ (旨味) は考慮していないため、的中率が高い=買うべき、ではありません。"
        )

# --- 予想履歴 ---
with tab_record:
    st.subheader("予想履歴と結果照合")
    stats = honmei_stats()
    if stats:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("照合済みレース", stats["races"])
        c2.metric("◎ 単勝的中率", f"{stats['win_rate']:.1%}")
        c3.metric("◎ 複勝的中率", f"{stats['fukusho_rate']:.1%}")
        c4.metric("◎ 単勝回収率", f"{stats['tansho_roi']:.1%}")
        st.caption("予想を保存したレースのうち、結果照合済みのものの実績です。")
    hist = list_predictions()
    if hist.empty:
        st.info("まだ予想履歴がありません。「予想」タブで予想すると自動で保存されます。")
    else:
        options = hist["race_id"].tolist()
        labels = {
            r["race_id"]: f"{r['race_date']} {r['place']}{r['race_no']}R "
                          f"{r['race_name']} (予想: {r['created_at']})"
            for _, r in hist.iterrows()
        }
        sel = st.selectbox("レースを選択", options, format_func=lambda x: labels[x])
        if st.button("結果を照合する", type="primary"):
            try:
                with st.spinner("結果を取得中..."):
                    merged, has_result = check_result(sel)
            except BlockedError as e:
                st.error(str(e))
                st.stop()
            if not has_result:
                st.warning("結果はまだ公開されていないようです。レース後にもう一度試してください。")
                st.dataframe(merged, hide_index=True, use_container_width=True)
            else:
                honmei = merged[merged["mark"] == "◎"]
                if len(honmei):
                    fin = honmei["finish"].iloc[0]
                    if pd.isna(fin):
                        st.info("◎の馬は出走しなかったようです (取消・除外)。")
                    elif fin == 1:
                        st.success(f"🎯 ◎ {honmei['horse_name'].iloc[0]} が1着! 単勝的中!")
                    elif fin <= 3:
                        st.success(f"✅ ◎ {honmei['horse_name'].iloc[0]} は{int(fin)}着。複勝圏内!")
                    else:
                        st.error(f"❌ ◎ {honmei['horse_name'].iloc[0]} は{int(fin)}着でした。")
                view = merged.rename(columns={
                    "pred_rank": "予想順位", "mark": "印", "horse_no": "馬番",
                    "horse_name": "馬名", "p_top3": "複勝圏確率", "p_win": "予測勝率",
                    "finish": "着順", "odds": "単勝オッズ", "popularity": "人気",
                })
                st.dataframe(view, hide_index=True, use_container_width=True)

# --- 回収率検証 ---
with tab_backtest:
    st.subheader("単勝回収率バックテスト")
    st.info(
        "**回収率は80%が「平均」です。** JRAの単勝払戻率は80% (控除率20%) のため、"
        "でたらめに買っても長期的には80%に収束します。"
        "80%を上回っていればモデルに予測力があり、100%超で初めて利益になります。\n\n"
        "券種ごとの基準: 単勝・複勝 80% / 枠連・馬連・ワイド 77.5% / "
        "馬単・三連複 75% / 三連単 72.5%"
    )
    st.caption(
        "モデルが学習に使っていない検証期間のレースで計算します。"
        "確定オッズを使うため実際よりやや楽観的な数字になります。"
    )
    c1, c2 = st.columns(2)
    with c1:
        ev = st.slider("期待値買いの閾値 (予測勝率×オッズ)", 1.0, 2.0, 1.2, 0.05)
    with c2:
        max_odds = st.slider("期待値買いのオッズ上限", 10, 300, 100, 10)
    if st.button("バックテスト実行", type="primary"):
        if not MODEL_PATH.exists():
            st.error("モデルが未学習です。先に「学習」タブで学習してください。")
        else:
            try:
                with st.spinner("計算中..."):
                    res = backtest(ev_threshold=ev, max_odds=float(max_odds))
                st.caption(f"検証期間: {res['valid_from']} 以降 / {res['n_races']:,}レース")
                rows = []
                for name, s in res["strategies"].items():
                    roi = s["roi"]
                    rows.append({
                        "戦略": name,
                        "ベット数": s["bets"],
                        "的中数": s["hits"],
                        "的中率": f"{s['hit_rate']:.1%}" if s["hit_rate"] is not None else "-",
                        "投資額": f"{s['stake']:,}円",
                        "払戻額": f"{s['payout']:,}円",
                        "回収率": f"{roi:.1%}" if roi is not None else "-",
                        "控除率比": f"{roi - 0.80:+.1%}" if roi is not None else "-",
                    })
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                st.caption(
                    "「控除率比」がプラスならモデルに予測力があります。"
                    "ただしベット数が数百点以下の戦略は、1回の高配当で数字が大きく動くため"
                    "偶然の可能性が高い点に注意してください。"
                )
            except RuntimeError as e:
                st.error(str(e))

    st.divider()
    st.subheader("1日いくら儲かる? (日別収支シミュレーション)")
    st.caption(
        "1日の予算を決めて、その日のレースに等分して買った場合の日別収支を"
        "実際の配当で計算します。予算内に収まるよう確信度の高いレースから買います。"
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        ds_budget = st.number_input("1日の予算(円)", 1000, 1000000, 10000, step=1000)
    with c2:
        ds_strategy = st.selectbox(
            "買い方", list(DAY_STRATEGIES),
            format_func=lambda k: f"{k} — {DAY_STRATEGIES[k][0]}",
        )
    with c3:
        if ds_strategy == EV_STRATEGY:
            ds_ev = st.slider("期待値の閾値", 1.0, 1.5, 1.10, 0.05, key="ds_ev")
            ds_conf = 0.0
        else:
            ds_conf = st.slider("確信度で絞る (0=全レース)", 0.0, 0.8, 0.5, 0.05,
                                key="ds_conf")
            ds_ev = 1.10
    if ds_strategy == EV_STRATEGY:
        st.warning(
            "⚠️ **実験的な戦略です。** 期待値が高い=人気薄の馬を買うため、"
            "平均収支はプラスに見えても**大半の日は全額外れ**、"
            "たまの高配当で辻褄が合う形になります。"
            "検証点数がまだ少なく、統計的に利益が出ると結論できる段階ではありません。"
        )
    if st.button("日別収支をシミュレーション"):
        try:
            with st.spinner("計算中... (数分かかります)"):
                res = simulate_days(int(ds_budget), ds_strategy, ds_conf,
                                    ev_threshold=ds_ev)
            if res["summary"] is None:
                st.warning("対象レースがありませんでした。")
            else:
                s = res["summary"]
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("1日の平均収支", f"{s['平均収支']:+,}円")
                c2.metric("1日の中央値収支", f"{s['中央値収支']:+,}円",
                          help="半分の日はこれより悪い結果です。"
                               "平均が高くても中央値が大きなマイナスなら、"
                               "たまの高配当に依存した不安定な戦略です。")
                c3.metric("プラスになった日", f"{s['プラスの日']}/{s['日数']}日 "
                                        f"({s['プラス率']:.0%})")
                c4.metric("最高の日 / 最悪の日",
                          f"{s['最高']:+,} / {s['最低']:+,}円")
                c5.metric("通算回収率", f"{s['通算回収率']:.1%}")
                if s["平均収支"] > 0 and s["中央値収支"] < 0:
                    st.error(
                        f"平均は +{s['平均収支']:,}円ですが**中央値は "
                        f"{s['中央値収支']:,}円**です。"
                        "利益が数回の高配当に集中しており、実際にはほとんどの日が"
                        "マイナスになります。安定した収入源にはなりません。"
                    )
                st.caption(
                    f"通算: {s['合計投資']:,}円 投資 → {s['合計払戻']:,}円 払戻 "
                    f"(収支 {s['通算収支']:+,}円 / {s['日数']}日)"
                )
                d = res["daily"].copy()
                d["回収率"] = d["回収率"].map(
                    lambda v: f"{v:.0%}" if pd.notna(v) else "-")
                st.dataframe(d, hide_index=True, use_container_width=True)
                st.caption(
                    "⚠️ 控除率20〜25%があるため、平均収支はマイナスになるのが普通です。"
                    "この数字は過去データ上の結果であり、将来の成績を保証しません。"
                )
        except Exception as e:
            st.error(f"計算に失敗しました: {e}")

    st.divider()
    st.subheader("券種別の回収率 (実際の払戻データ)")
    st.caption(
        "予想タブが提案する買い目をそのまま全レースで買った場合の回収率を、"
        "JRA-VAN の実際の配当で計算します。"
    )
    use_ability = st.checkbox("能力モデルで検証 (既定は市場モデル)")
    if st.button("券種別シミュレーションを実行"):
        try:
            with st.spinner("計算中... (数分かかります)"):
                res = betsim_simulate(use_market=not use_ability)
            st.caption(
                f"{res['model']} / 検証期間 {res['valid_from']} 以降 / "
                f"{res['n_races']:,}レース"
            )
            t = res["table"].copy()
            t["的中率"] = t["的中率"].map(lambda v: f"{v:.1%}" if pd.notna(v) else "-")
            t["回収率"] = t["回収率"].map(lambda v: f"{v:.1%}" if pd.notna(v) else "-")
            st.dataframe(t, hide_index=True, use_container_width=True)
            st.caption("回収率の高い券種に絞ると収支が改善しやすくなります。")
        except Exception as e:
            st.error(f"計算に失敗しました: {e}")

st.divider()
st.caption(
    "⚠️ 本アプリは学習・研究目的のツールです。予想・買い目は的中を保証するものではなく、"
    "馬券の購入は自己責任で行ってください。データ取得は netkeiba の個人利用の範囲で行っています。"
)
