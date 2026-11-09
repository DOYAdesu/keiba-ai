"""勝率から Harville 近似で券種ごとの的中確率を見積もり、買い目を提案する。

Harville モデル: 2着は「残った馬の勝率を再正規化した確率」で決まるという近似。
"""
import itertools

import numpy as np
import pandas as pd

MARKS = ["◎", "○", "▲", "△", "△"]


def normalize_win_probs(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, None)
    return p / p.sum()


def quinella_prob(p, i, j):
    """馬連: i, j が1・2着 (順不同)"""
    return p[i] * p[j] / (1 - p[i]) + p[j] * p[i] / (1 - p[j])


def trio_prob(p, i, j, k):
    """三連複: i, j, k が3着以内 (順不同)"""
    total = 0.0
    for a, b, c in itertools.permutations((i, j, k)):
        d1 = 1 - p[a]
        d2 = 1 - p[a] - p[b]
        if d1 > 1e-9 and d2 > 1e-9:
            total += p[a] * (p[b] / d1) * (p[c] / d2)
    return total


def wide_prob(p, i, j):
    """ワイド: i, j がともに3着以内"""
    return sum(trio_prob(p, i, j, k) for k in range(len(p)) if k not in (i, j))


def suggest_bets(pred_df):
    """予想結果 (印順にソート済み、p_win_norm / p_top3 列を持つ) から買い目を作る。

    返り値: DataFrame(券種, 買い目, 点数, 推定的中率)
    """
    df = pred_df.reset_index(drop=True)
    col = "p_win_for_bets" if "p_win_for_bets" in df.columns else "p_win_norm"
    p = normalize_win_probs(df[col].values)
    no = df["horse_no"].astype(int).tolist()
    n = len(df)

    def name(i):
        return f"{no[i]} {df['horse_name'].iloc[i]}"

    bets = []
    A = 0  # ◎
    top3_col = "m_top3" if "m_top3" in df.columns else "p_top3"
    bets.append(("単勝", f"◎ {name(A)}", 1, p[A]))
    bets.append(("複勝", f"◎ {name(A)}", 1, float(df[top3_col].iloc[A])))

    if n >= 2:
        bets.append(("馬連", f"◎-○ {no[A]}-{no[1]}", 1, quinella_prob(p, A, 1)))
        bets.append(("ワイド", f"◎-○ {no[A]}-{no[1]}", 1, wide_prob(p, A, 1)))
    if n >= 3:
        bets.append(("ワイド", f"◎-▲ {no[A]}-{no[2]}", 1, wide_prob(p, A, 2)))

    # 三連複 ◎軸・相手○▲△△ の2頭選び (最大6点)
    partners = [i for i in range(1, min(5, n))]
    if len(partners) >= 2:
        combos = list(itertools.combinations(partners, 2))
        prob = sum(trio_prob(p, A, i, j) for i, j in combos)
        aite = "".join(MARKS[i] for i in partners)
        aite_no = ",".join(str(no[i]) for i in partners)
        bets.append(
            ("三連複", f"◎{no[A]}軸 - 相手{aite}({aite_no})", len(combos), prob)
        )

    out = pd.DataFrame(bets, columns=["券種", "買い目", "点数", "推定的中率"])
    out["推定的中率"] = out["推定的中率"].astype(float).round(3)
    return out
