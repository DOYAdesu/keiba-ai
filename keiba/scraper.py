"""netkeiba (db.netkeiba.com) から中央・地方のレース結果を収集して SQLite に保存する。

個人利用の範囲で、1リクエストごとに WAIT_SEC 秒待機してサーバーに負荷をかけない。
"""
import argparse
import datetime as dt
import re
import sqlite3
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "keiba.db"

RACE_URL = "https://db.netkeiba.com/race/{race_id}/"
HORSE_PED_URL = "https://db.netkeiba.com/horse/ped/{horse_id}/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}
WAIT_SEC = 1.5


class BlockedError(RuntimeError):
    """netkeiba 側にアクセス制限された可能性が高いときに送出。"""

JRA_PLACES = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}
NAR_PLACES = {
    "30": "門別", "35": "盛岡", "36": "水沢", "42": "浦和", "43": "船橋",
    "44": "大井", "45": "川崎", "46": "金沢", "47": "笠松", "48": "名古屋",
    "50": "園田", "51": "姫路", "54": "高知", "55": "佐賀", "65": "帯広ば",
}
PLACES = {**JRA_PLACES, **NAR_PLACES}


def connect(db_path=DB_PATH):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS results (
            race_id TEXT, date TEXT, place TEXT, race_no INTEGER, race_name TEXT,
            surface TEXT, distance INTEGER, weather TEXT, going TEXT, is_nar INTEGER,
            finish INTEGER, waku INTEGER, horse_no INTEGER, horse_id TEXT, horse_name TEXT,
            sex TEXT, age INTEGER, kinryo REAL, jockey_id TEXT, jockey_name TEXT,
            time_sec REAL, odds REAL, popularity INTEGER, weight REAL, weight_diff REAL,
            PRIMARY KEY (race_id, horse_no)
        );
        CREATE TABLE IF NOT EXISTS scraped (
            race_id TEXT PRIMARY KEY, status TEXT
        );
        CREATE TABLE IF NOT EXISTS horses (
            horse_id TEXT PRIMARY KEY, sire_id TEXT, sire_name TEXT,
            dam_sire_id TEXT, dam_sire_name TEXT
        );
        CREATE TABLE IF NOT EXISTS payouts (
            race_id TEXT, bet_type TEXT, combo TEXT, pay INTEGER, popularity INTEGER,
            PRIMARY KEY (race_id, bet_type, combo)
        );
        CREATE TABLE IF NOT EXISTS workouts (
            horse_id TEXT, date TEXT, time4f REAL, time3f REAL,
            time2f REAL, lap1f REAL,
            PRIMARY KEY (horse_id, date)
        );
        CREATE TABLE IF NOT EXISTS entries (
            race_id TEXT, horse_no INTEGER, waku INTEGER, horse_id TEXT,
            horse_name TEXT, sex TEXT, age INTEGER, kinryo REAL,
            jockey_id TEXT, jockey_name TEXT, weight REAL, weight_diff REAL,
            odds REAL,
            PRIMARY KEY (race_id, horse_no)
        );
        CREATE TABLE IF NOT EXISTS races (
            race_id TEXT PRIMARY KEY, date TEXT, place TEXT, race_no INTEGER,
            race_name TEXT, surface TEXT, distance INTEGER, going TEXT, weather TEXT,
            post_time TEXT, field_size INTEGER, grade TEXT
        );
        CREATE TABLE IF NOT EXISTS predictions (
            race_id TEXT, horse_no INTEGER, horse_name TEXT, mark TEXT,
            pred_rank INTEGER, p_top3 REAL, p_win REAL,
            race_date TEXT, place TEXT, race_no INTEGER, race_name TEXT,
            created_at TEXT,
            PRIMARY KEY (race_id, horse_no)
        );
        """
    )
    # 後から増やした列は jvbridge.migrate が一括で面倒を見る
    from .jvbridge import migrate

    migrate(conn)
    return conn


_block_streak = 0


# 回線の瞬断やタイムアウトで何時間分もの収集が落ちないよう、少し粘る
NET_RETRIES = 4
NET_BACKOFF = 5.0


def request_with_retry(url, timeout=20):
    """GET する。タイムアウト・接続エラーは間をあけて数回やり直す。

    それでも駄目なら None。1件のためにバッチ全体を落とさないため。
    """
    for attempt in range(NET_RETRIES):
        try:
            return requests.get(url, headers=HEADERS, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            if attempt == NET_RETRIES - 1:
                print(f"通信に失敗したためスキップ: {url} ({e})", flush=True)
                return None
            time.sleep(NET_BACKOFF * (attempt + 1))
    return None


def fetch_html(url, encoding="EUC-JP"):
    global _block_streak
    resp = request_with_retry(url)
    time.sleep(WAIT_SEC)
    if resp is None:
        return None
    # WAF ブロックの特徴: 400/403 で本文が空
    if resp.status_code in (400, 403) and not resp.text:
        _block_streak += 1
        if _block_streak >= 5:
            raise BlockedError(
                "netkeiba からのアクセスが制限されている可能性があります。"
                "数時間〜半日おいてから再開してください (取得済みデータは保持されます)。"
            )
        return None
    _block_streak = 0
    if resp.status_code != 200:
        return None
    resp.encoding = encoding
    return resp.text


def _to_seconds(text):
    m = re.match(r"(?:(\d+):)?(\d+\.\d+)", text or "")
    if not m:
        return None
    minutes = int(m.group(1)) if m.group(1) else 0
    return minutes * 60 + float(m.group(2))


def parse_race_page(html, race_id):
    """db.netkeiba.com のレース結果ページを解析する。テーブルが無ければ None。"""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="race_table_01")
    if table is None:
        return None

    intro = soup.find("div", class_="data_intro")
    intro_text = intro.get_text(" ", strip=True) if intro else ""
    h1 = intro.find("h1") if intro else None
    race_name = h1.get_text(strip=True) if h1 else ""

    m = re.search(r"([芝ダ障])\D*?(\d{3,4})m", intro_text)
    surface = m.group(1) if m else None
    distance = int(m.group(2)) if m else None
    m = re.search(r"天候\s*:\s*(\S+?)\s*/", intro_text)
    weather = m.group(1) if m else None
    m = re.search(r"(?:芝|ダート)\s*:\s*(\S+?)\s*/", intro_text)
    going = m.group(1) if m else None
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", intro_text)
    date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None

    place_code = race_id[4:6]
    info = {
        "race_id": race_id,
        "date": date,
        "place": PLACES.get(place_code, place_code),
        "race_no": int(race_id[-2:]),
        "race_name": race_name,
        "surface": surface,
        "distance": distance,
        "weather": weather,
        "going": going,
        "is_nar": 1 if place_code in NAR_PLACES else 0,
    }

    trs = table.find_all("tr")
    headers = [th.get_text(strip=True) for th in trs[0].find_all("th")]
    col = {name: i for i, name in enumerate(headers)}

    def cell(tds, name):
        i = col.get(name)
        return tds[i] if i is not None and i < len(tds) else None

    rows = []
    for tr in trs[1:]:
        tds = tr.find_all("td")
        if not tds:
            continue
        finish_text = cell(tds, "着順").get_text(strip=True) if cell(tds, "着順") else ""
        if not finish_text.isdigit():
            continue  # 中止・除外・取消・失格

        row = {"finish": int(finish_text)}
        for key, name in [("waku", "枠番"), ("horse_no", "馬番"), ("popularity", "人気")]:
            td = cell(tds, name)
            t = td.get_text(strip=True) if td else ""
            row[key] = int(t) if t.isdigit() else None

        td = cell(tds, "馬名")
        a = td.find("a", href=re.compile(r"/horse/")) if td else None
        row["horse_name"] = a.get_text(strip=True) if a else (td.get_text(strip=True) if td else "")
        m = re.search(r"/horse/(\w+)", a["href"]) if a else None
        row["horse_id"] = m.group(1) if m else None

        td = cell(tds, "性齢")
        m = re.match(r"(牡|牝|セ|せん|騙)(\d+)", td.get_text(strip=True) if td else "")
        row["sex"] = m.group(1) if m else None
        row["age"] = int(m.group(2)) if m else None

        td = cell(tds, "斤量")
        try:
            row["kinryo"] = float(td.get_text(strip=True))
        except (AttributeError, ValueError):
            row["kinryo"] = None

        td = cell(tds, "騎手")
        a = td.find("a", href=re.compile(r"/jockey/")) if td else None
        row["jockey_name"] = a.get_text(strip=True) if a else (td.get_text(strip=True) if td else "")
        m = re.search(r"/jockey/(?:result/recent/)?(\w+)", a["href"]) if a else None
        row["jockey_id"] = m.group(1) if m else None

        td = cell(tds, "タイム")
        row["time_sec"] = _to_seconds(td.get_text(strip=True)) if td else None

        td = cell(tds, "単勝")
        try:
            row["odds"] = float(td.get_text(strip=True))
        except (AttributeError, ValueError):
            row["odds"] = None

        td = cell(tds, "馬体重")
        m = re.match(r"(\d+)\(([-+]?\d+)\)", td.get_text(strip=True) if td else "")
        row["weight"] = float(m.group(1)) if m else None
        row["weight_diff"] = float(m.group(2)) if m else None

        td = cell(tds, "通過")
        m = re.match(r"(\d+)", td.get_text(strip=True) if td else "")
        row["corner1"] = int(m.group(1)) if m else None

        td = cell(tds, "上り")
        try:
            row["agari"] = float(td.get_text(strip=True))
        except (AttributeError, ValueError):
            row["agari"] = None

        td = cell(tds, "調教師")
        a = td.find("a", href=re.compile(r"/trainer/")) if td else None
        # 表示は "[東] 田中一誠" の形なので所属の括弧を落とす
        row["trainer_name"] = (
            re.sub(r"^\[.\]\s*", "", a.get_text(strip=True)) if a else None
        )
        m = re.search(r"/trainer/(?:result/recent/)?(\w+)", a["href"]) if a else None
        row["trainer_id"] = m.group(1) if m else None

        rows.append(row)
    return info, rows


def parse_race_extras(html):
    """レース単位の付随情報 (ラップ・前後半3F・3,4コーナーの通過順) を取り出す。

    netkeiba の result_table_02 に「ラップ」「ペース」「3コーナー」の行がある。
    """
    soup = BeautifulSoup(html, "lxml")
    out = {"lap": None, "pace_s3": None, "pace_l3": None,
           "corner3": None, "corner4": None}
    for tb in soup.select("table.result_table_02"):
        for tr in tb.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            head = cells[0].get_text(strip=True)
            body = cells[1].get_text(" ", strip=True).replace("\xa0", " ")
            if head == "ラップ":
                laps = re.findall(r"\d+\.\d", body)
                if laps:
                    out["lap"] = ",".join(laps)
            elif head == "ペース":
                m = re.search(r"\((\d+\.\d)\s*-\s*(\d+\.\d)\)", body)
                if m:
                    out["pace_s3"] = float(m.group(1))
                    out["pace_l3"] = float(m.group(2))
            elif head == "3コーナー":
                out["corner3"] = body or None
            elif head == "4コーナー":
                out["corner4"] = body or None
    return out


RACE_EXTRA_COLS = ("lap", "pace_s3", "pace_l3", "corner3", "corner4")


def save_race_extras(conn, race_id, info, extras):
    """races テーブルにレース単位の情報を入れる (無ければ作る)。"""
    conn.execute(
        "INSERT OR IGNORE INTO races (race_id) VALUES (?)", (race_id,)
    )
    sets = ["date=?", "place=?", "race_no=?", "race_name=?", "surface=?",
            "distance=?", "going=?", "weather=?"]
    vals = [info.get("date"), info.get("place"), info.get("race_no"),
            info.get("race_name"), info.get("surface"), info.get("distance"),
            info.get("going"), info.get("weather")]
    sets += [f"{c}=?" for c in RACE_EXTRA_COLS]
    vals += [extras.get(c) for c in RACE_EXTRA_COLS]
    conn.execute(
        f"UPDATE races SET {', '.join(sets)} WHERE race_id=?", vals + [race_id]
    )


def backfill_race(conn, race_id):
    """既に取得済みのレースに調教師とラップだけを足す。

    返り値: 'saved' / 'none'(ページ無し)
    """
    html = fetch_html(RACE_URL.format(race_id=race_id))
    if html is None:
        return "none"
    parsed = parse_race_page(html, race_id)
    if parsed is None:
        return "none"
    info, rows = parsed
    for row in rows:
        conn.execute(
            "UPDATE results SET trainer_id=?, trainer_name=? "
            "WHERE race_id=? AND horse_no=?",
            (row.get("trainer_id"), row.get("trainer_name"),
             race_id, row.get("horse_no")),
        )
    save_race_extras(conn, race_id, info, parse_race_extras(html))
    conn.commit()
    return "saved"


def backfill(db_path=DB_PATH, limit=None, jra_only=True):
    """調教師・ラップが未取得のレースを順に埋めるジェネレータ。"""
    conn = connect(db_path)
    try:
        sql = ("SELECT DISTINCT r.race_id FROM results r "
               "LEFT JOIN races ra ON ra.race_id = r.race_id "
               "WHERE r.trainer_name IS NULL "
               + ("AND r.is_nar=0 " if jra_only else "")
               + "ORDER BY r.date DESC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        ids = [r[0] for r in conn.execute(sql)]
        total = len(ids)
        yield f"調教師・ラップの補完対象: {total:,}レース (約{total // 60 + 1}分)"
        n_saved = n_none = 0
        for i, race_id in enumerate(ids, 1):
            if backfill_race(conn, race_id) == "saved":
                n_saved += 1
            else:
                n_none += 1
            if i % 20 == 0 or i == total:
                yield f"補完 {i:,}/{total:,}レース (取得{n_saved:,} / 無し{n_none:,})"
    finally:
        conn.close()
    yield "補完終了"


def save_race(conn, info, rows):
    for row in rows:
        rec = {**info, **row}
        cols = ", ".join(rec)
        ph = ", ".join("?" * len(rec))
        conn.execute(
            f"INSERT OR REPLACE INTO results ({cols}) VALUES ({ph})", list(rec.values())
        )
    conn.commit()


def scrape_race(conn, race_id):
    """1レース取得。返り値: 'saved' / 'skip'(取得済) / 'none'(存在しない)

    status は 'ok'(旧: 通過・上り無し) / 'ok2'(現行) / 'none' の3種。
    """
    cur = conn.execute("SELECT status FROM scraped WHERE race_id=?", (race_id,))
    hit = cur.fetchone()
    if hit:
        return "skip" if hit[0] in ("ok", "ok2") else "none"

    html = fetch_html(RACE_URL.format(race_id=race_id))
    parsed = parse_race_page(html, race_id) if html else None
    if parsed is None:
        conn.execute("INSERT OR REPLACE INTO scraped VALUES (?, 'none')", (race_id,))
        conn.commit()
        return "none"

    info, rows = parsed
    save_race(conn, info, rows)
    save_race_extras(conn, race_id, info, parse_race_extras(html))
    conn.execute("INSERT OR REPLACE INTO scraped VALUES (?, 'ok2')", (race_id,))
    conn.commit()
    return "saved"


def race_exists(conn, race_id):
    return scrape_race(conn, race_id) in ("saved", "skip")


def collect_jra(year_from, year_to, places=None, db_path=DB_PATH):
    """中央競馬の結果を収集するジェネレータ。進捗メッセージを yield する。"""
    conn = connect(db_path)
    places = places or list(JRA_PLACES)
    saved = 0
    try:
        for year in range(year_from, year_to + 1):
            for pcode in places:
                pname = JRA_PLACES[pcode]
                for kai in range(1, 7):
                    first_day_ok = False
                    for day in range(1, 13):
                        rid1 = f"{year}{pcode}{kai:02d}{day:02d}01"
                        if not race_exists(conn, rid1):
                            break
                        first_day_ok = True
                        saved += 1
                        for r in range(2, 13):
                            rid = f"{year}{pcode}{kai:02d}{day:02d}{r:02d}"
                            if scrape_race(conn, rid) == "none":
                                break
                            saved += 1
                        yield f"{year} {pname} {kai}回{day}日目 取得完了 (累計{saved}R)"
                    if not first_day_ok:
                        break  # この回が無ければ以降の回も無い
            yield f"=== {year}年 完了 ==="
    finally:
        conn.close()
    yield f"中央 収集終了: {saved}レース"


def collect_nar(date_from, date_to, places=None, db_path=DB_PATH):
    """地方競馬の結果を収集するジェネレータ。race_id は YYYY+場+MMDD+R。"""
    conn = connect(db_path)
    places = places or list(NAR_PLACES)
    saved = 0
    try:
        d = date_from
        while d <= date_to:
            mmdd = f"{d.month:02d}{d.day:02d}"
            for pcode in places:
                rid1 = f"{d.year}{pcode}{mmdd}01"
                if not race_exists(conn, rid1):
                    continue  # この日この場は非開催
                saved += 1
                for r in range(2, 13):
                    rid = f"{d.year}{pcode}{mmdd}{r:02d}"
                    if scrape_race(conn, rid) == "none":
                        break
                    saved += 1
                yield f"{d} {NAR_PLACES[pcode]} 取得完了 (累計{saved}R)"
            d += dt.timedelta(days=1)
    finally:
        conn.close()
    yield f"地方 収集終了: {saved}レース"


def parse_horse_page(html):
    """5代血統ページ (blood_table) から父・母父を取り出す。

    rowspan=16 の td が [父, 母]、rowspan=8 の td が [父父, 父母, 母父, 母母]。
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="blood_table")
    if table is None:
        return None
    tds = table.find_all("td")
    r16 = [td for td in tds if td.get("rowspan") == "16"]
    r8 = [td for td in tds if td.get("rowspan") == "8"]
    if not r16:
        return None

    def link_info(td):
        if td is None:
            return None, None
        a = td.find("a", href=re.compile(r"/horse/"))
        if not a:
            return None, td.get_text(strip=True)
        m = re.search(r"/horse/(?:ped/)?(\w+)", a["href"])
        return (m.group(1) if m else None), a.get_text(strip=True)

    sire_id, sire_name = link_info(r16[0])
    dam_sire_id, dam_sire_name = link_info(r8[2] if len(r8) >= 3 else None)
    return {
        "sire_id": sire_id, "sire_name": sire_name,
        "dam_sire_id": dam_sire_id, "dam_sire_name": dam_sire_name,
    }


def collect_pedigree(db_path=DB_PATH):
    """results にいて horses に無い馬の血統を収集するジェネレータ。"""
    conn = connect(db_path)
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT horse_id FROM results "
            "WHERE horse_id IS NOT NULL "
            "AND horse_id NOT IN (SELECT horse_id FROM horses)"
        )]
        total = len(ids)
        yield f"血統未取得の馬: {total}頭 (約{total // 60 + 1}分)"
        for i, horse_id in enumerate(ids, 1):
            html = fetch_html(HORSE_PED_URL.format(horse_id=horse_id))
            if html is None:
                continue  # 取得失敗 (ブロック等)。保存せず後日リトライできるようにする
            ped = parse_horse_page(html)
            if ped is None:
                ped = {"sire_id": None, "sire_name": None,
                       "dam_sire_id": None, "dam_sire_name": None}
            conn.execute(
                "INSERT OR REPLACE INTO horses VALUES (?, ?, ?, ?, ?)",
                (horse_id, ped["sire_id"], ped["sire_name"],
                 ped["dam_sire_id"], ped["dam_sire_name"]),
            )
            conn.commit()
            if i % 50 == 0 or i == total:
                yield f"血統取得 {i}/{total}頭"
    finally:
        conn.close()
    yield "血統収集終了"


def refresh_v1_races(db_path=DB_PATH):
    """旧バージョンで取得したレース(通過・上り無し)を再取得するジェネレータ。"""
    conn = connect(db_path)
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT race_id FROM scraped WHERE status='ok'"
        )]
        total = len(ids)
        yield f"再取得対象: {total}レース (約{total // 60 + 1}分)"
        for i, race_id in enumerate(ids, 1):
            conn.execute("DELETE FROM scraped WHERE race_id=?", (race_id,))
            conn.commit()
            scrape_race(conn, race_id)
            if i % 20 == 0 or i == total:
                yield f"再取得 {i}/{total}レース"
    finally:
        conn.close()
    yield "再取得終了"


def main():
    ap = argparse.ArgumentParser(description="netkeiba レース結果収集")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("jra", help="中央競馬を収集")
    p.add_argument("year_from", type=int)
    p.add_argument("year_to", type=int)
    p.add_argument("--places", nargs="*", help="場コード (01-10)")

    p = sub.add_parser("nar", help="地方競馬を収集")
    p.add_argument("date_from", help="YYYY-MM-DD")
    p.add_argument("date_to", help="YYYY-MM-DD")
    p.add_argument("--places", nargs="*", help="場コード (30,44 など)")

    sub.add_parser("pedigree", help="血統(父・母父)を収集")
    sub.add_parser("refresh", help="旧形式レースの通過・上りを再取得")

    p = sub.add_parser("backfill", help="調教師・ラップを既存レースに補完")
    p.add_argument("--limit", type=int, help="先頭N件だけ処理する")
    p.add_argument("--all", action="store_true", help="地方競馬も対象にする")

    args = ap.parse_args()
    if args.cmd == "jra":
        gen = collect_jra(args.year_from, args.year_to, args.places)
    elif args.cmd == "nar":
        gen = collect_nar(
            dt.date.fromisoformat(args.date_from),
            dt.date.fromisoformat(args.date_to),
            args.places,
        )
    elif args.cmd == "pedigree":
        gen = collect_pedigree()
    elif args.cmd == "backfill":
        gen = backfill(limit=args.limit, jra_only=not args.all)
    else:
        gen = refresh_v1_races()
    try:
        for msg in gen:
            print(msg, flush=True)
    except BlockedError as e:
        print(f"中断: {e}", flush=True)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
