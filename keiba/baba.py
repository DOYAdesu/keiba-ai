"""JRA の馬場情報 (クッション値・含水率) を取り込む。

  python -m keiba.baba archive 2020 2026   # 過去の開催ごとの PDF (JRA 公式) を取り込む
  python -m keiba.baba current             # 今週の測定値 (馬場情報ページ) を取り込む

含水率は2018年7月から、クッション値は2020年9月11日から公表されている。
PDF は data/baba_pdf/ に保存し、取得済みは読み直さない (JRA に負担をかけない)。
"""
import argparse
import datetime as dt
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

from .scraper import DB_PATH

BASE = "https://www.jra.go.jp/keiba/baba/"
PDF_DIR = Path(__file__).resolve().parent.parent / "data" / "baba_pdf"
UA = {"User-Agent": "Mozilla/5.0 (keiba-ai personal research)"}
WAIT = 1.5  # JRA へのアクセス間隔 (秒)

PLACES = {
    "sapporo": "札幌", "hakodate": "函館", "fukushima": "福島", "niigata": "新潟",
    "tokyo": "東京", "nakayama": "中山", "chukyo": "中京", "kyoto": "京都",
    "hanshin": "阪神", "kokura": "小倉",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS baba (
    date TEXT, place TEXT, course TEXT, cushion REAL, cushion_at TEXT,
    mt_goal REAL, mt_4c REAL, md_goal REAL, md_4c REAL, raceday INTEGER, src TEXT,
    PRIMARY KEY (date, place)
)
"""


def connect(db_path=None):
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.execute(SCHEMA)
    return conn


def _get(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r


def _num(t):
    try:
        return float(t)
    except (TypeError, ValueError):
        return None


# 例: "第 1日 10月 3日 土曜日 A 07:00 9.7 05:00 14.9 13.4 7.2 7.2"
#     "10月 2日 金曜日 A 09:00 10.1 08:30 12.7 11.8 8.2 8.4"  (金曜の測定)
ROW = re.compile(
    r"^(第\s*\d+日\s+)?(\d+)月\s*(\d+)日\s+(\S)曜日\s+(.*)$"
)
NUM = r"(\d+\.\d|－|-|―)"


def parse_pdf(path, year, place):
    import pdfplumber

    rows = []
    with pdfplumber.open(str(path)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    if not re.search(r"第\s*\d+日\s+\d+月", text):
        return parse_old(text, year, place)
    for line in text.splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        raceday, mon, day, _, rest = m.groups()
        toks = rest.split()
        # クッション値の公表前 (2020年9月以前) は 使用コース・含水率だけ
        course = toks[0] if toks and re.fullmatch(r"[A-DＡ-Ｄ]", toks[0]) else None
        if course:
            toks = toks[1:]
        times = [t for t in toks if re.fullmatch(r"\d{1,2}:\d{2}", t)]
        vals = [t for t in toks if not re.fullmatch(r"\d{1,2}:\d{2}", t)]
        vals = [_num(v) for v in vals]
        cushion = None
        cushion_at = None
        if len(vals) >= 5:
            cushion, vals = vals[0], vals[1:]
            cushion_at = times[0] if times else None
        vals = (vals + [None] * 4)[:4]
        y = year
        rows.append({
            "date": f"{y}-{int(mon):02d}-{int(day):02d}", "place": place,
            "course": course, "cushion": cushion, "cushion_at": cushion_at,
            "mt_goal": vals[0], "mt_4c": vals[1], "md_goal": vals[2], "md_4c": vals[3],
            "raceday": 1 if raceday else 0, "src": "pdf",
        })
    return rows


WDAY = {"金": 4, "土": 5, "日": 6, "月": 0, "火": 1, "水": 2, "木": 3}


def parse_old(text, year, place):
    """2018〜2021年ごろの書式: 週ごとの表 (列が 金曜日 土曜日 日曜日 [月曜日])。"""
    rows = {}
    start = None
    cols = []
    for raw in text.splitlines():
        line = raw.strip()
        m = re.search(r"(\d{4})年(\d+)月(\d+)日(?:から|～|~)", line)
        if m:
            start = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            cols = []
            continue
        if start is None:
            continue
        days = re.findall(r"(.)曜日", line)
        if days and not re.search(r"\d+\.\d", line):
            # 先頭の測定日 (多くは金曜) から曜日ごとに日付を進める
            cols, d = [], start
            for w in days:
                for _ in range(7):
                    if d.weekday() == WDAY.get(w, d.weekday()):
                        break
                    d += dt.timedelta(days=1)
                cols.append(d)
            continue
        vals = [_num(v) for v in re.findall(r"\d+\.\d", line)]
        if not cols or len(vals) != len(cols):
            continue
        if "クッション" in line:
            key = "cushion"
        elif "芝" in line and "含水" in line:
            key = "mt"
        elif "ダート" in line:
            key = "md"
        elif "コーナー" in line:
            key = "4c"
        else:
            continue
        for d, v in zip(cols, vals):
            r = rows.setdefault(d, {"date": d.isoformat(), "place": place, "src": "pdf",
                                    "raceday": 0 if d.weekday() == 4 else 1})
            if key == "cushion":
                r["cushion"] = v
            elif key in ("mt", "md"):
                r[f"{key}_goal"] = v
                r["_last"] = key
            else:  # 直前のゴール前の行に対応する4コーナー
                r[f"{r.get('_last', 'mt')}_4c"] = v
    for r in rows.values():
        r.pop("_last", None)
    return list(rows.values())


def save(conn, rows):
    cols = ["date", "place", "course", "cushion", "cushion_at", "mt_goal", "mt_4c",
            "md_goal", "md_4c", "raceday", "src"]
    conn.executemany(
        f"INSERT OR REPLACE INTO baba ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [tuple(r.get(c) for c in cols) for r in rows],
    )
    conn.commit()


def archive(year_from, year_to, db_path=None, log=print):
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    this_year = dt.date.today().year
    total = 0
    try:
        for y in range(year_from, year_to + 1):
            idx = BASE + ("archive/" if y == this_year else f"archive/{y}.html")
            html = _get(idx).content.decode("cp932", errors="replace")
            links = sorted(set(re.findall(rf"/keiba/baba/archive/{y}pdf/([a-z]+)(\d+)\.pdf", html)))
            time.sleep(WAIT)
            for name, kai in links:
                place = PLACES.get(name)
                if not place:
                    continue
                path = PDF_DIR / f"{y}_{name}{kai}.pdf"
                # 今年の分は開催が終わるまで更新されるので取り直す
                if not path.exists() or y == this_year:
                    path.write_bytes(_get(f"https://www.jra.go.jp/keiba/baba/archive/{y}pdf/{name}{kai}.pdf").content)
                    time.sleep(WAIT)
                rows = parse_pdf(path, y, place)
                save(conn, rows)
                total += len(rows)
            log(f"{y}年: {len(links)}開催")
    finally:
        conn.close()
    log(f"馬場情報: {total}件")
    return total


def _jp_time(s, today):
    """'11月8日（日曜）7時00分' → ('2026-11-08', '07:00')"""
    m = re.search(r"(\d+)月(\d+)日.*?(\d+)時(\d+)分", s)
    if not m:
        return None, None
    mon, day, hh, mm = map(int, m.groups())
    y = today.year
    if mon == 12 and today.month == 1:
        y -= 1
    return f"{y}-{mon:02d}-{day:02d}", f"{hh:02d}:{mm:02d}"


def current(db_path=None, log=print):
    """馬場情報ページ (今週分) のクッション値・含水率を取り込む。"""
    today = dt.date.today()
    got = {}
    cush = _get(BASE + "_data_cushion.html").content.decode("cp932", errors="replace")
    time.sleep(WAIT)
    moist = _get(BASE + "_data_moist.html").content.decode("cp932", errors="replace")
    for block in re.findall(r'<div id="rc\w" title="([^"]+)">(.*?)</div><!-- /\[#rc', cush, re.S):
        place, body = block
        for t, v in re.findall(r'class="time">([^<]+)</div>\s*<div class="cushion">([^<]*)<', body):
            d, hm = _jp_time(t, today)
            if d:
                got.setdefault((d, place), {})
                got[(d, place)].update(cushion=_num(v.strip()), cushion_at=hm)
    for place, body in re.findall(r'<div id="rc\w" title="([^"]+)">(.*?)</div><!-- /\[#rc', moist, re.S):
        for unit in re.split(r'<div class="unit">', body)[1:]:
            tm = re.search(r'class="time">([^<]+)<', unit)
            if not tm:
                continue
            d, _ = _jp_time(tm.group(1), today)
            turf = re.search(r'class="turf">.*?class="mg"[^>]*>([^<]*)<.*?class="m4c"[^>]*>([^<]*)<', unit, re.S)
            dirt = re.search(r'class="dirt">.*?class="mg"[^>]*>([^<]*)<.*?class="m4c"[^>]*>([^<]*)<', unit, re.S)
            r = got.setdefault((d, place), {})
            if turf:
                r.update(mt_goal=_num(turf.group(1)), mt_4c=_num(turf.group(2)))
            if dirt:
                r.update(md_goal=_num(dirt.group(1)), md_4c=_num(dirt.group(2)))
    rows = []
    for (d, place), r in got.items():
        wd = dt.date.fromisoformat(d).weekday()
        rows.append(dict(r, date=d, place=place, raceday=0 if wd == 4 else 1, src="live"))
    conn = connect(db_path)
    try:
        # PDF の確定値があればそちらを優先 (コース A/B/C も入っている)
        have = {(a, b) for a, b in conn.execute("SELECT date, place FROM baba WHERE src='pdf'")}
        save(conn, [r for r in rows if (r["date"], r["place"]) not in have])
    finally:
        conn.close()
    log(f"今週の馬場情報: {len(rows)}件 ({', '.join(sorted({p for _, p in got}))})")
    return rows


def race_baba(conn, race_rows):
    """レース (date, place, surface) ごとの馬場の数値。当日朝の値、無ければ3日以内の直近 (金曜) 。

    race_rows: DataFrame (race_id, date, place, surface)。返り値: DataFrame (race_id, cushion, moist, ...)
    """
    import pandas as pd

    b = pd.read_sql("SELECT * FROM baba", conn)
    if b.empty or race_rows.empty:
        return pd.DataFrame(columns=["race_id", "cushion", "moist", "moist_4c", "course"])
    b["d"] = pd.to_datetime(b["date"])
    r = race_rows[["race_id", "date", "place", "surface"]].copy()
    r["d"] = pd.to_datetime(r["date"])
    r = r.sort_values("d")
    b = b.sort_values("d")
    m = pd.merge_asof(r, b.drop(columns=["date"]), on="d", by="place",
                      tolerance=pd.Timedelta(days=3), direction="backward")
    turf = m["surface"] == "芝"
    out = pd.DataFrame({
        "race_id": m["race_id"],
        "cushion": m["cushion"].where(turf),
        "moist": m["mt_goal"].where(turf, m["md_goal"]),
        "moist_4c": m["mt_4c"].where(turf, m["md_4c"]),
        "course": m["course"],
        "baba_date": m["d"].dt.strftime("%Y-%m-%d"),
    })
    return out


def main():
    ap = argparse.ArgumentParser(description="JRA 馬場情報 (クッション値・含水率)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("archive")
    p.add_argument("year_from", type=int)
    p.add_argument("year_to", type=int)
    sub.add_parser("current")
    a = ap.parse_args()
    try:
        if a.cmd == "archive":
            archive(a.year_from, a.year_to)
        else:
            current()
    except requests.RequestException as e:
        print(f"取得に失敗: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
