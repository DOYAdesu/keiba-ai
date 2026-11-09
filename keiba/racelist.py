"""日付からレース (race_id) を探す。

- 地方 (NAR): race_id が「年+場+月日+R」なのでスクレイピング不要で組み立てられる。
- 中央 (JRA): race_id の回・日目は日付から計算できないため、
  出馬表ページを少しずつ探索して該当日の開催を見つける (場ごとに数十秒)。
"""
import re
import time

import requests

from .scraper import HEADERS, JRA_PLACES, WAIT_SEC

SHUTUBA_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def nar_race_ids(date, place_code):
    """地方: 指定日・場の 1〜12R の race_id を組み立てる (存在確認なし)。"""
    prefix = f"{date.year}{place_code}{date.month:02d}{date.day:02d}"
    return [f"{prefix}{r:02d}" for r in range(1, 13)]


def _fetch_shutuba_date(race_id):
    """出馬表ページの開催日を返す。ページが無ければ None。"""
    import datetime as dt

    resp = requests.get(SHUTUBA_URL.format(race_id=race_id), headers=HEADERS, timeout=20)
    time.sleep(WAIT_SEC)
    if resp.status_code != 200:
        return None
    resp.encoding = resp.apparent_encoding
    m = DATE_RE.search(resp.text[:30000])
    if not m:
        return None
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def find_jra_races(date, place_code, progress=None):
    """中央: 指定日・場の race_id 12個を探索して返す。開催が無ければ []。

    progress: 進捗メッセージを受け取るコールバック (省略可)。
    """
    for kai in range(1, 7):
        day = 1
        last_ok = 0  # ページが存在した最後の日目
        while day <= 12:
            race_id = f"{date.year}{place_code}{kai:02d}{day:02d}01"
            if progress:
                progress(f"{JRA_PLACES[place_code]} {kai}回{day}日目 を確認中...")
            page_date = _fetch_shutuba_date(race_id)
            if page_date is None:
                if last_ok and day > last_ok + 1:
                    day = last_ok + 1  # 飛ばしすぎた可能性 → 1日ずつ確認に戻る
                    continue
                break  # この回はここで終わり → 次の回へ
            if page_date == date:
                prefix = f"{date.year}{place_code}{kai:02d}{day:02d}"
                return [f"{prefix}{r:02d}" for r in range(1, 13)]
            if page_date > date:
                return []  # この場の開催は指定日を跨いで進んでいる → 該当なし
            # まだ過去の日付: 日数差に応じて少し先へ飛ぶ
            last_ok = day
            diff = (date - page_date).days
            day += max(1, min(diff // 3, 4))
    return []
