"""JRA-VAN Data Lab (JV-Link) から中央競馬データを取得して SQLite に保存するブリッジ。

JV-Link は 32bit COM のため、このスクリプトは【32bit Python】で実行する。
依存は pywin32 のみ (pandas 等は不要)。64bit 側からは keiba/jvlink.py 経由で呼ばれる。

使い方 (32bit Python で):
  python jvbridge.py settings                # JV-Link 設定画面を開く (利用キー入力)
  python jvbridge.py race 20240101           # レース成績 (RA+SE) を取得
  python jvbridge.py horse 20240101          # 馬マスタ/血統 (UM) を取得

レコード仕様: JV-Data仕様書 Ver.4.9 準拠 (バイト位置は仕様書の通り)。
race_id は netkeiba と同一形式 (年+場+回+日目+R) で保存するため、既存データと共存できる。
"""
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "keiba.db"

BUFFER_SIZE = 200000

JRA_PLACES = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}
WEATHER = {"1": "晴", "2": "曇", "3": "雨", "4": "小雨", "5": "雪", "6": "小雪"}
GOING = {"1": "良", "2": "稍重", "3": "重", "4": "不良"}
SEX = {"1": "牡", "2": "牝", "3": "セ"}

# 競走種別コード → 年齢条件
SYUBETU = {
    "11": "2歳", "12": "3歳", "13": "3歳以上", "14": "4歳以上",
    "18": "障害3歳以上", "19": "障害4歳以上",
}
# 競走条件コード → クラス
JYOKEN = {
    "701": "新馬", "702": "未出走", "703": "未勝利", "999": "オープン",
    "005": "1勝クラス", "010": "2勝クラス", "016": "3勝クラス",
}

# results テーブル定義は keiba/scraper.py と同一に保つこと
SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    race_id TEXT, date TEXT, place TEXT, race_no INTEGER, race_name TEXT,
    surface TEXT, distance INTEGER, weather TEXT, going TEXT, is_nar INTEGER,
    finish INTEGER, waku INTEGER, horse_no INTEGER, horse_id TEXT, horse_name TEXT,
    sex TEXT, age INTEGER, kinryo REAL, jockey_id TEXT, jockey_name TEXT,
    time_sec REAL, odds REAL, popularity INTEGER, weight REAL, weight_diff REAL,
    corner1 INTEGER, agari REAL, trainer_id TEXT, trainer_name TEXT,
    PRIMARY KEY (race_id, horse_no)
);
CREATE TABLE IF NOT EXISTS scraped (race_id TEXT PRIMARY KEY, status TEXT);
CREATE TABLE IF NOT EXISTS horses (
    horse_id TEXT PRIMARY KEY, sire_id TEXT, sire_name TEXT,
    dam_sire_id TEXT, dam_sire_name TEXT
);
CREATE TABLE IF NOT EXISTS payouts (
    race_id TEXT, bet_type TEXT, combo TEXT, pay INTEGER, popularity INTEGER,
    PRIMARY KEY (race_id, bet_type, combo)
);
-- 坂路調教 (HC レコード)
CREATE TABLE IF NOT EXISTS workouts (
    horse_id TEXT, date TEXT, time4f REAL, time3f REAL, time2f REAL, lap1f REAL,
    PRIMARY KEY (horse_id, date)
);
-- 出馬表 (出走前を含む)。予想はここから読むので JV への問い合わせが不要になる
CREATE TABLE IF NOT EXISTS entries (
    race_id TEXT, horse_no INTEGER, waku INTEGER, horse_id TEXT, horse_name TEXT,
    sex TEXT, age INTEGER, kinryo REAL, jockey_id TEXT, jockey_name TEXT,
    weight REAL, weight_diff REAL, odds REAL, trainer_id TEXT, trainer_name TEXT,
    PRIMARY KEY (race_id, horse_no)
);
-- 出走前を含む全レースの基本情報 (レース一覧用)
-- lap: 1ハロンごとのラップ秒をカンマ区切りで保存 (例 "12.3,10.9,...")
CREATE TABLE IF NOT EXISTS races (
    race_id TEXT PRIMARY KEY, date TEXT, place TEXT, race_no INTEGER,
    race_name TEXT, surface TEXT, distance INTEGER, going TEXT, weather TEXT,
    post_time TEXT, field_size INTEGER, grade TEXT,
    lap TEXT, pace_s3 REAL, pace_s4 REAL, pace_l3 REAL, pace_l4 REAL
);
"""

# 既存DBに後から足した列 (テーブル名 -> [(列名, 型), ...])
ADDED_COLUMNS = {
    "results": [("corner1", "INTEGER"), ("agari", "REAL"),
                ("trainer_id", "TEXT"), ("trainer_name", "TEXT")],
    "entries": [("trainer_id", "TEXT"), ("trainer_name", "TEXT")],
    "races": [("lap", "TEXT"), ("pace_s3", "REAL"), ("pace_s4", "REAL"),
              ("pace_l3", "REAL"), ("pace_l4", "REAL"),
              ("corner3", "TEXT"), ("corner4", "TEXT")],
    # 「無かった」記録をいつ確かめたか。開催前に見て無かったレースを
    # 永久に無いことにしないよう、古い記録は確かめ直す
    "scraped": [("checked_at", "TEXT")],
}


def migrate(conn):
    """後から増やした列を既存DBに足す (何度実行してもよい)。"""
    for table, columns in ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, type_ in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {type_}")
    conn.commit()


def log(msg):
    print(msg, flush=True)


TOKU_SCHEMA = """
-- 特別登録 (TK)。出馬表が出る前 (日〜水) に分かる、特別レースの登録馬
CREATE TABLE IF NOT EXISTS tokubetsu (
    race_id TEXT, num INTEGER, horse_id TEXT, horse_name TEXT, sex TEXT,
    trainer_id TEXT, trainer_name TEXT, kinryo REAL,
    PRIMARY KEY (race_id, num)
);
"""


def open_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.executescript(TOKU_SCHEMA)
    migrate(conn)
    return conn


# JVRead の BSTR は「元バイト列を cp1252 相当で復号した文字列」として届くため、
# 1文字=1バイトで正確に元へ戻す (0x80-0x9F は cp1252 の特殊文字にマップされる)。
CP1252_TO_BYTE = {
    0x20AC: 0x80, 0x201A: 0x82, 0x0192: 0x83, 0x201E: 0x84, 0x2026: 0x85,
    0x2020: 0x86, 0x2021: 0x87, 0x02C6: 0x88, 0x2030: 0x89, 0x0160: 0x8A,
    0x2039: 0x8B, 0x0152: 0x8C, 0x017D: 0x8E, 0x2018: 0x91, 0x2019: 0x92,
    0x201C: 0x93, 0x201D: 0x94, 0x2022: 0x95, 0x2013: 0x96, 0x2014: 0x97,
    0x02DC: 0x98, 0x2122: 0x99, 0x0161: 0x9A, 0x203A: 0x9B, 0x0153: 0x9C,
    0x017E: 0x9E, 0x0178: 0x9F,
}


def _latin_bytes(s):
    """1文字=1バイトとみなして復元 (COM が cp1252 相当で復号した場合)。"""
    b = bytearray()
    for ch in s:
        o = ord(ch)
        b.append(o if o < 0x100 else CP1252_TO_BYTE.get(o, 0x3F))
    return bytes(b)


def bstr_to_bytes(s, expected=None):
    """JVRead の文字列を元のバイト列に戻す。

    pywin32 の COM マーシャリングは環境によって
    (a) cp1252 相当で1文字=1バイト、または (b) cp932 で正しく復号
    のどちらかになる。expected (JVRead が返したバイト数) と一致する方を採用する。
    """
    latin = _latin_bytes(s)
    if expected is None or len(latin) == expected:
        return latin
    try:
        cp932 = s.encode("cp932", errors="replace")
        if len(cp932) == expected:
            return cp932
    except Exception:
        pass
    return latin


class JVLink:
    def __init__(self):
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self.jv = win32com.client.Dispatch("JVDTLab.JVLink")

    def init(self):
        ret = self.jv.JVInit("UNKNOWN")
        if ret != 0:
            raise RuntimeError(
                f"JVInit 失敗 (code={ret})。JV-Link の利用キーが未設定の可能性があります。"
                "『settings』コマンドで設定画面を開き、JRA-VAN Data Lab の利用キーを入力してください。"
            )

    def settings(self):
        self.jv.JVSetUIProperties()

    def rt_open(self, dataspec, key):
        """速報データを開く。返り値: 0=成功, -1=該当データなし。

        速報データは提供期間が短く、期限切れや対象外だと -1/-303/-403 が返る。
        これらは異常ではないので -1 に丸める。
        """
        result = self.jv.JVRTOpen(dataspec, key)
        ret = result[0] if isinstance(result, tuple) else result
        if ret in (-1, -303, -403, -411, -412, -431):
            return -1
        if ret < 0:
            raise RuntimeError(f"JVRTOpen 失敗 (code={ret})")
        return ret

    def open(self, dataspec, fromtime, option=1, retries=8):
        ret, read_count, dl_count = -999, 0, 0
        for attempt in range(retries):
            result = self.jv.JVOpen(dataspec, fromtime, option)
            if isinstance(result, tuple):
                ret, read_count, dl_count = result[0], result[1], result[2]
            else:
                ret, read_count, dl_count = result, 0, 0
            # -501: 前回セッションの後始末中に出ることがあるため待って再試行
            if ret == -501 and attempt < retries - 1:
                log(f"JV-Link が使用中の可能性 (code=-501)。30秒待って再試行します "
                    f"({attempt + 1}/{retries - 1})...")
                try:
                    self.jv.JVClose()
                except Exception:
                    pass
                time.sleep(30)
                continue
            break
        if ret < -2:
            hints = {
                -301: "認証エラー: 利用キーが正しいか確認してください",
                -302: "利用キーの有効期限切れ",
                -303: "利用キーが未設定です (settings で設定)",
                -501: "開始日がセットアップデータの提供範囲外です。開始日を新しくしてください (例: 2024-01-01)",
                -504: "JRA-VAN サーバーメンテナンス中",
            }
            hint = hints.get(ret, "")
            raise RuntimeError(f"JVOpen 失敗 (code={ret}) {hint}")
        return ret, read_count, dl_count

    def read_records(self):
        """(レコード種別, bytes) を yield する。"""
        while True:
            result = self.jv.JVRead("", BUFFER_SIZE, "")
            ret = result[0] if isinstance(result, tuple) else result
            if ret == 0:
                break  # 全件終了
            if ret == -1:
                continue  # ファイル切替
            if ret == -3:
                time.sleep(1)  # ダウンロード中
                continue
            if ret < 0:
                raise RuntimeError(f"JVRead 失敗 (code={ret})")
            raw = bstr_to_bytes(result[1], ret)[:ret]
            yield raw[0:2].decode("ascii", errors="replace"), raw

    def close(self):
        try:
            self.jv.JVClose()
        except Exception:
            pass


def _s(raw, start, end):
    """バイト位置 [start:end) を cp932 文字列で取り出す (仕様書の位置-1)。"""
    return raw[start:end].decode("cp932", errors="replace").strip()


def _int(text):
    try:
        v = int(text)
        return v
    except (TypeError, ValueError):
        return None


def parse_ra(raw):
    """RA: レース詳細 (天候・馬場は位置888〜890 = JVData_Struct準拠)"""
    if len(raw) < 890:
        return None
    year = _s(raw, 11, 15)
    monthday = _s(raw, 15, 19)
    jyo = _s(raw, 19, 21)
    kaiji = _s(raw, 21, 23)
    nichiji = _s(raw, 23, 25)
    race_no = _s(raw, 25, 27)
    if jyo not in JRA_PLACES:
        return None  # 地方・海外は対象外
    track = _int(_s(raw, 705, 707)) or 0
    if 10 <= track <= 22:
        surface = "芝"
    elif 23 <= track <= 29:
        surface = "ダ"
    elif track >= 51:
        surface = "障"
    else:
        surface = None
    name = _s(raw, 32, 92) or _s(raw, 572, 592) or _s(raw, 637, 697)
    if not name:
        # 特別名の無いレースは「3歳未勝利」のように条件から組み立てる
        age = SYUBETU.get(_s(raw, 616, 618), "")
        jyoken = _s(raw, 634, 637)  # 最若年条件
        name = (age + JYOKEN.get(jyoken, "")).strip()
    going_cd = _s(raw, 888, 889) if surface != "ダ" else _s(raw, 889, 890)
    grade_cd = _s(raw, 614, 615)
    hasso = _s(raw, 873, 877)  # 発走時刻 HHMM
    laps, s3, s4, l3, l4 = parse_ra_lap(raw, _int(_s(raw, 697, 701)))
    return {
        "race_id": f"{year}{jyo}{kaiji}{nichiji}{race_no}",
        "date": f"{year}-{monthday[:2]}-{monthday[2:]}",
        "place": JRA_PLACES[jyo],
        "race_no": _int(race_no),
        "race_name": name,
        "surface": surface,
        "distance": _int(_s(raw, 697, 701)),
        "weather": WEATHER.get(_s(raw, 887, 888)),
        "going": GOING.get(going_cd),
        "is_nar": 0,
        "_post_time": f"{hasso[:2]}:{hasso[2:]}" if len(hasso) == 4 and hasso.isdigit() else None,
        "_field_size": _int(_s(raw, 883, 885)),
        "_grade": {"A": "G1", "B": "G2", "C": "G3", "D": "重賞",
                   "L": "L", "F": "J.G1", "G": "J.G2", "H": "J.G3"}.get(grade_cd),
        "_lap": ",".join(f"{v:.1f}" for v in laps) if laps else None,
        "_pace_s3": s3, "_pace_s4": s4, "_pace_l3": l3, "_pace_l4": l4,
    }


# RA レコードの後半。馬場状態 (DirtBabaCD) の直後からラップが並ぶ。
# 天候=888 を基準にした JVData_Struct 準拠の位置 (1始まり)。
LAP_START = 890          # LapTime[25] (各3バイト) = 1始まり891
LAP_COUNT = 25
HARON_START = 969        # HaronTimeS3/S4/L3/L4 (各3バイト) = 1始まり970


def _tenths(raw, start):
    """3バイトの「0.1秒単位」を秒に直す。未設定 (000/999/空白) は None。"""
    t = _s(raw, start, start + 3)
    if not t.isdigit() or t in ("000", "999"):
        return None
    return int(t) / 10.0


def parse_ra_lap(raw, distance):
    """RA レコードからラップと前後半3F/4Fを取り出す。

    返り値: (ラップ秒のリスト, 前半3F, 前半4F, 上がり3F, 上がり4F)
    ラップは距離から求まるハロン数だけ採用する (余りは 000 で埋まっている)。
    """
    if len(raw) < HARON_START + 12:
        return [], None, None, None, None

    n = min(round(distance / 200), LAP_COUNT) if distance else LAP_COUNT
    laps = []
    for i in range(n):
        v = _tenths(raw, LAP_START + i * 3)
        if v is None:
            break
        laps.append(v)
    # 1ハロン 9〜20秒の範囲に収まらなければ位置がずれていると判断して捨てる
    if laps and not all(9.0 <= v <= 20.0 for v in laps):
        laps = []
    return (
        laps,
        _tenths(raw, HARON_START),
        _tenths(raw, HARON_START + 3),
        _tenths(raw, HARON_START + 6),
        _tenths(raw, HARON_START + 9),
    )


def parse_se(raw):
    """SE: 馬毎レース情報"""
    if len(raw) < 553:
        return None
    year = _s(raw, 11, 15)
    jyo = _s(raw, 19, 21)
    if jyo not in JRA_PLACES:
        return None
    race_id = f"{year}{jyo}{_s(raw, 21, 23)}{_s(raw, 23, 25)}{_s(raw, 25, 27)}"

    finish = _int(_s(raw, 334, 336))
    ijyo = _s(raw, 331, 332)
    if not finish or ijyo in ("1", "2", "3", "4"):
        return None  # 未確定・取消・除外・中止

    t = _s(raw, 338, 342)
    time_sec = None
    if t.isdigit() and int(t) > 0:
        t = t.zfill(4)  # 空白詰めで桁落ちする場合があるため
        time_sec = int(t[0]) * 60 + int(t[1:3]) + int(t[3]) / 10.0

    odds_t = _s(raw, 359, 363)
    odds = int(odds_t) / 10.0 if odds_t.isdigit() and int(odds_t) > 0 else None

    weight_t = _s(raw, 324, 327)
    weight = float(weight_t) if weight_t.isdigit() and weight_t not in ("000", "999") else None
    fugo = _s(raw, 327, 328)
    sa = _s(raw, 328, 331)
    weight_diff = None
    if sa.isdigit() and sa != "999" and weight is not None:
        weight_diff = float(sa) * (-1 if fugo == "-" else 1)

    corner1 = None
    for a, b in ((351, 353), (353, 355), (355, 357), (357, 359)):
        v = _int(_s(raw, a, b))
        if v:
            corner1 = v
            break

    agari_t = _s(raw, 390, 393)
    agari = int(agari_t) / 10.0 if agari_t.isdigit() and agari_t not in ("000", "999") else None

    futan = _s(raw, 288, 291)
    ninki = _int(_s(raw, 363, 365))
    return {
        "race_id": race_id,
        "finish": finish,
        "waku": _int(_s(raw, 27, 28)),
        "horse_no": _int(_s(raw, 28, 30)),
        "horse_id": _s(raw, 30, 40),
        "horse_name": _s(raw, 40, 76),
        "sex": SEX.get(_s(raw, 78, 79)),
        "age": _int(_s(raw, 82, 84)),
        "kinryo": int(futan) / 10.0 if futan.isdigit() and int(futan) > 0 else None,
        "jockey_id": _s(raw, 296, 301),
        "jockey_name": _s(raw, 306, 314),
        "trainer_id": _s(raw, 85, 90),
        "trainer_name": _s(raw, 90, 98),
        "time_sec": time_sec,
        "odds": odds,
        "popularity": ninki if ninki else None,
        "weight": weight,
        "weight_diff": weight_diff,
        "corner1": corner1,
        "agari": agari,
    }


# HR (払戻) の各券種のレイアウト
# (券種名, 開始位置(1始まり), 繰り返し数, 1件のバイト長, 組番の桁数, 人気の桁数)
HR_LAYOUT = [
    ("単勝", 103, 3, 13, 2, 2),
    ("複勝", 142, 5, 13, 2, 2),
    ("枠連", 207, 3, 13, 2, 2),
    ("馬連", 246, 3, 16, 4, 3),
    ("ワイド", 294, 7, 16, 4, 3),
    ("馬単", 454, 6, 16, 4, 3),
    ("三連複", 550, 3, 18, 6, 3),
    ("三連単", 604, 6, 19, 6, 4),
]


def parse_hr(raw):
    """HR: 払戻情報。返り値: (race_id, [(券種, 組番, 払戻金, 人気), ...])"""
    if len(raw) < 718:
        return None
    jyo = _s(raw, 19, 21)
    if jyo not in JRA_PLACES:
        return None
    race_id = f"{_s(raw, 11, 15)}{jyo}{_s(raw, 21, 23)}{_s(raw, 23, 25)}{_s(raw, 25, 27)}"

    out = []
    for name, start, count, size, combo_len, ninki_len in HR_LAYOUT:
        for i in range(count):
            base = start - 1 + i * size  # 仕様書は1始まり
            combo = _s(raw, base, base + combo_len)
            pay = _s(raw, base + combo_len, base + combo_len + 9)
            ninki = _s(raw, base + combo_len + 9, base + combo_len + 9 + ninki_len)
            if not combo or not combo.strip("0") or not pay.isdigit() or int(pay) == 0:
                continue
            out.append((name, combo, int(pay), _int(ninki)))
    return race_id, out


def parse_hc(raw):
    """HC: 坂路調教。タイムは1/10秒単位の文字列。"""
    if len(raw) < 58:
        return None
    horse_id = _s(raw, 24, 34)
    if not horse_id.strip():
        return None

    def t(a, b):
        v = _s(raw, a, b)
        return int(v) / 10.0 if v.isdigit() and int(v) > 0 else None

    d = _s(raw, 12, 20)
    if len(d) != 8 or not d.isdigit():
        return None
    return {
        "horse_id": horse_id,
        "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
        "time4f": t(34, 38), "time3f": t(41, 45),
        "time2f": t(48, 52), "lap1f": t(55, 58),
    }


def parse_um(raw):
    """UM: 競走馬マスタ (3代血統: 1=父, 5=母父)"""
    horse_id = _s(raw, 11, 21)
    if not horse_id:
        return None

    def ketto(idx):  # idx: 1始まり
        pos = 204 + (idx - 1) * 46
        return _s(raw, pos, pos + 10), _s(raw, pos + 10, pos + 46)

    sire_id, sire_name = ketto(1)
    dam_sire_id, dam_sire_name = ketto(5)
    return {
        "horse_id": horse_id,
        "sire_id": sire_id or None, "sire_name": sire_name or None,
        "dam_sire_id": dam_sire_id or None, "dam_sire_name": dam_sire_name or None,
    }


def save_result_row(conn, info, row):
    rec = {k: v for k, v in {**info, **row}.items() if not k.startswith("_")}
    rec["race_id"] = row["race_id"]
    cols = ", ".join(rec)
    ph = ", ".join("?" * len(rec))
    conn.execute(f"INSERT OR REPLACE INTO results ({cols}) VALUES ({ph})",
                 list(rec.values()))


ENTRY_DB_COLS = ("waku", "horse_id", "horse_name", "sex", "age", "kinryo",
                 "jockey_id", "jockey_name", "trainer_id", "trainer_name",
                 "weight", "weight_diff", "odds")


def save_entry_row(conn, race_id, e):
    """出馬表1頭分を entries テーブルに保存する。"""
    conn.execute(
        "INSERT OR REPLACE INTO entries (race_id, horse_no, " +
        ", ".join(ENTRY_DB_COLS) + ") VALUES (?, ?, " +
        ", ".join("?" * len(ENTRY_DB_COLS)) + ")",
        [race_id, e["horse_no"]] + [e.get(c) for c in ENTRY_DB_COLS],
    )


def save_race_info(conn, race_id, info):
    """出走前を含む全レースの基本情報を races テーブルに保存する。"""
    conn.execute(
        "INSERT OR REPLACE INTO races (race_id, date, place, race_no, race_name,"
        " surface, distance, going, weather, post_time, field_size, grade,"
        " lap, pace_s3, pace_s4, pace_l3, pace_l4)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (race_id, info.get("date"), info.get("place"), info.get("race_no"),
         info.get("race_name"), info.get("surface"), info.get("distance"),
         info.get("going"), info.get("weather"), info.get("_post_time"),
         info.get("_field_size"), info.get("_grade"),
         info.get("_lap"), info.get("_pace_s3"), info.get("_pace_s4"),
         info.get("_pace_l3"), info.get("_pace_l4")),
    )


def cmd_race(from_yyyymmdd, db_path, setup=False):
    conn = open_db(db_path)
    jv = JVLink()
    jv.init()
    # option=1 (通常) は直近1年程度のみ。古い期間は option=4 (セットアップ) で取る
    ret, read_count, dl_count = jv.open(
        "RACE", from_yyyymmdd + "000000", 4 if setup else 1
    )
    if ret in (-1, -2):
        log("新しいデータはありません。古い期間を取るには --setup を付けてください。")
        return
    log(f"JRA-VAN からダウンロード開始 (対象 {read_count:,} レコード, DL {dl_count:,} ファイル)")

    races = {}          # race_id -> RA info
    pending = {}        # race_id -> [SE rows] (RA 未到着分)
    saved_races = set() # 出走行を1件以上保存したレース
    n_ra = n_se = n_hr = 0
    diag = {}           # 診断: SE を捨てた理由の集計
    try:
        for spec, raw in jv.read_records():
            if spec == "SE":
                key = ("len<553" if len(raw) < 553
                       else "非JRA" if _s(raw, 19, 21) not in JRA_PLACES
                       else "着順なし" if not _int(_s(raw, 334, 336))
                       else "取消除外" if _s(raw, 331, 332) in ("1", "2", "3", "4")
                       else "OK")
                diag[key] = diag.get(key, 0) + 1
            if spec == "RA":
                info = parse_ra(raw)
                if info is None:
                    continue
                rid = info.pop("race_id")
                races[rid] = info
                save_race_info(conn, rid, info)
                n_ra += 1
                for row in pending.pop(rid, []):
                    save_result_row(conn, races[rid], row)
                    saved_races.add(rid)
            elif spec == "SE":
                # 出馬表としても保存する (着順が無い=これからのレースも対象)
                entry = parse_se_entry(raw)
                if entry and entry["horse_no"]:
                    jyo = _s(raw, 19, 21)
                    erid = (f"{_s(raw, 11, 15)}{jyo}{_s(raw, 21, 23)}"
                            f"{_s(raw, 23, 25)}{_s(raw, 25, 27)}")
                    save_entry_row(conn, erid, entry)

                row = parse_se(raw)
                if row is None:
                    continue
                rid = row["race_id"]
                if rid in races:
                    save_result_row(conn, races[rid], row)
                    saved_races.add(rid)
                else:
                    pending.setdefault(rid, []).append(row)
                n_se += 1
            elif spec == "HR":
                parsed = parse_hr(raw)
                if parsed is None:
                    continue
                rid, pays = parsed
                conn.executemany(
                    "INSERT OR REPLACE INTO payouts VALUES (?, ?, ?, ?, ?)",
                    [(rid, t, c, p, n) for t, c, p, n in pays],
                )
                n_hr += len(pays)
            if (n_ra + n_se) % 2000 == 0 and (n_ra + n_se) > 0:
                conn.commit()
                log(f"取得中... レース {n_ra:,} / 出走 {n_se:,}")
    finally:
        for rid, rows in pending.items():
            if rid in races:
                for row in rows:
                    save_result_row(conn, races[rid], row)
                saved_races.add(rid)
        # 出走行を実際に保存できたレースだけ「取得済み」にする
        # (出馬表段階のレースをマークすると後で結果が取れなくなるため)
        conn.executemany(
            "INSERT OR REPLACE INTO scraped (race_id, status) VALUES (?, 'ok2')",
            [(rid,) for rid in saved_races],
        )
        conn.commit()
        jv.close()
        conn.close()
    log(f"JRA-VAN 成績取得 完了: レース {n_ra:,} / 出走 {n_se:,} / 払戻 {n_hr:,}")
    if diag:
        log("SE内訳: " + ", ".join(f"{k}={v:,}" for k, v in sorted(diag.items())))
    if n_ra and not n_se:
        log("警告: 出走データが1件も取れていません。セットアップデータのダウンロードが"
            "未完了の可能性があります。ダウンロード完了後にもう一度実行するか、"
            "セットアップを使わない通常モード (約1年前まで) をお試しください。")


def cmd_rtrace(race_id, yyyymmdd, db_path):
    """速報データ (JVRTOpen) で当日レースの結果を取得する。

    蓄積系 (JVOpen) は当日結果の反映が遅いため、レース直後はこちらを使う。
    速報キー: YYYYMMDD + 場(2) + 回(2) + 日目(2) + R(2) = 16桁
    """
    key = yyyymmdd + race_id[4:6] + race_id[6:8] + race_id[8:10] + race_id[10:12]
    conn = open_db(db_path)
    jv = JVLink()
    jv.init()
    info = None
    rows = []
    try:
        if jv.rt_open("0B12", key) == -1:
            log("速報データはまだありません。")
            return
        for spec, raw in jv.read_records():
            if spec == "RA":
                parsed = parse_ra(raw)
                if parsed:
                    parsed.pop("race_id", None)
                    info = parsed
            elif spec == "SE":
                row = parse_se(raw)
                if row:
                    rows.append(row)
            elif spec == "HR":
                parsed = parse_hr(raw)
                if parsed:
                    rid, pays = parsed
                    conn.executemany(
                        "INSERT OR REPLACE INTO payouts VALUES (?, ?, ?, ?, ?)",
                        [(rid, t, c, p, n) for t, c, p, n in pays],
                    )
        if info is None or not rows:
            log("速報データに確定結果が含まれていません (レース前・発走直後の可能性)。")
            return
        for row in rows:
            save_result_row(conn, info, row)
        conn.execute("INSERT OR REPLACE INTO scraped (race_id, status) VALUES (?, 'ok2')", (race_id,))
        conn.commit()
        log(f"速報から結果を取得: {len(rows)}頭")
    finally:
        jv.close()
        conn.close()


def parse_se_entry(raw):
    """SE レコードから出馬表としての情報を取り出す (着順が無くても可)。"""
    if len(raw) < 553:
        return None
    if _s(raw, 19, 21) not in JRA_PLACES:
        return None
    if _s(raw, 331, 332) in ("1", "2", "3"):
        return None  # 取消・除外
    futan = _s(raw, 288, 291)
    weight_t = _s(raw, 324, 327)
    weight = float(weight_t) if weight_t.isdigit() and weight_t not in ("000", "999") else None
    fugo, sa = _s(raw, 327, 328), _s(raw, 328, 331)
    weight_diff = None
    if sa.isdigit() and sa != "999" and weight is not None:
        weight_diff = float(sa) * (-1 if fugo == "-" else 1)
    odds_t = _s(raw, 359, 363)
    return {
        "waku": _int(_s(raw, 27, 28)),
        "horse_no": _int(_s(raw, 28, 30)),
        "horse_id": _s(raw, 30, 40),
        "horse_name": _s(raw, 40, 76),
        "sex": SEX.get(_s(raw, 78, 79)),
        "age": _int(_s(raw, 82, 84)),
        "kinryo": int(futan) / 10.0 if futan.isdigit() and int(futan) > 0 else None,
        "jockey_id": _s(raw, 296, 301),
        "jockey_name": _s(raw, 306, 314),
        "trainer_id": _s(raw, 85, 90),
        "trainer_name": _s(raw, 90, 98),
        "weight": weight,
        "weight_diff": weight_diff,
        "odds": int(odds_t) / 10.0 if odds_t.isdigit() and int(odds_t) > 0 else None,
    }


ENTRY_COLS = ("waku", "horse_no", "horse_id", "horse_name", "sex", "age",
              "kinryo", "jockey_id", "jockey_name", "trainer_id", "trainer_name",
              "weight", "weight_diff", "odds")


def _one_race_entries(jv, race_id, yyyymmdd):
    """1レース分の (info, rows) を速報から取る。"""
    key = yyyymmdd + race_id[4:6] + race_id[6:8] + race_id[8:10] + race_id[10:12]
    info, rows = None, []
    for spec in ("0B12", "0B11"):
        if jv.rt_open(spec, key) == -1:
            continue
        for s, raw in jv.read_records():
            if s == "RA" and info is None:
                parsed = parse_ra(raw)
                if parsed:
                    info = parsed
            elif s == "SE":
                e = parse_se_entry(raw)
                if e and e["horse_no"]:
                    rows.append(e)
        jv.close()
        if rows:
            break
    return info, sorted(rows, key=lambda x: x["horse_no"])


def _emit_entries(race_id, info, rows):
    if not rows:
        log(f"ENTRIES_NONE\t{race_id}")
        return
    if info:
        log(f"RACEINFO\t{race_id}\t" + "\t".join(str(info.get(k) or "") for k in
            ("race_name", "place", "race_no", "surface", "distance", "going", "date")))
    log(f"ENTRIES\t{race_id}\t" + "\t".join(ENTRY_COLS))
    for r in rows:
        log(f"ROW\t{race_id}\t" +
            "\t".join("" if r.get(c) is None else str(r[c]) for c in ENTRY_COLS))


def cmd_entries(race_ids, yyyymmdd, db_path=None):
    """速報データから出馬表を取得しTSVで出力する。race_ids はカンマ区切り可。"""
    jv = JVLink()
    jv.init()
    try:
        for rid in race_ids.split(","):
            rid = rid.strip()
            if not rid:
                continue
            info, rows = _one_race_entries(jv, rid, yyyymmdd)
            _emit_entries(rid, info, rows)
    finally:
        jv.close()


def cmd_daylist(yyyymmdd, db_path):
    """指定日のレース一覧を races テーブルから出力する (無ければ取得を促す)。"""
    conn = open_db(db_path)
    try:
        d = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
        rows = conn.execute(
            "SELECT race_id, place, race_no, race_name, surface, distance, "
            "post_time, grade FROM races WHERE date=? ORDER BY place, race_no", (d,)
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        log("DAYLIST_NONE")
        return
    for r in rows:
        log("RACE\t" + "\t".join("" if v is None else str(v) for v in r))


def cmd_rtodds(race_id, yyyymmdd, db_path=None):
    """速報オッズ (単勝) を取得して "馬番:オッズ" 形式で出力する。"""
    key = yyyymmdd + race_id[4:6] + race_id[6:8] + race_id[8:10] + race_id[10:12]
    jv = JVLink()
    jv.init()
    odds = {}
    try:
        for spec in ("0B15", "0B12", "0B31"):
            if jv.rt_open(spec, key) == -1:
                continue
            for s, raw in jv.read_records():
                if s == "SE":
                    row = parse_se(raw)
                    if row and row.get("odds"):
                        odds[row["horse_no"]] = row["odds"]
                elif s == "O1":
                    # 単複枠オッズ: 馬番ごと 単勝オッズ(4) + 人気(2) が 8バイト間隔
                    base = 27
                    for i in range(28):
                        pos = base + i * 8
                        if pos + 6 > len(raw):
                            break
                        t = _s(raw, pos, pos + 4)
                        if t.isdigit() and int(t) > 0:
                            odds.setdefault(i + 1, int(t) / 10.0)
            jv.close()
            if odds:
                break
    finally:
        jv.close()
    if not odds:
        log("ODDS_NONE")
        return
    log("ODDS " + ",".join(f"{k}:{v}" for k, v in sorted(odds.items())))


TK_HORSE_START = 655   # TokuUmaInfo[300] (各70バイト) = 1始まり656
TK_HORSE_LEN = 70


def parse_tk(raw):
    """TK: 特別登録馬。返り値: (レース情報 dict, 登録馬 list) / None

    先頭〜競走名は RA と同じ並び。その後ろは JVData_Struct の JV_TK_TOKUUMA 準拠
    (グレード615・条件616〜・距離637・トラック641・登録頭数653・馬は656から70バイトずつ)。
    """
    if len(raw) < TK_HORSE_START + TK_HORSE_LEN:
        return None
    year, monthday = _s(raw, 11, 15), _s(raw, 15, 19)
    jyo = _s(raw, 19, 21)
    if jyo not in JRA_PLACES:
        return None
    race_id = f"{year}{jyo}{_s(raw, 21, 23)}{_s(raw, 23, 25)}{_s(raw, 25, 27)}"
    track = _int(_s(raw, 640, 642)) or 0
    surface = ("芝" if 10 <= track <= 22 else "ダ" if 23 <= track <= 29
               else "障" if track >= 51 else None)
    name = _s(raw, 32, 92) or _s(raw, 572, 592)
    if not name:
        name = (SYUBETU.get(_s(raw, 615, 617), "")
                + JYOKEN.get(_s(raw, 633, 636), "")).strip()
    info = {
        "race_id": race_id,
        "date": f"{year}-{monthday[:2]}-{monthday[2:]}",
        "place": JRA_PLACES[jyo],
        "race_no": _int(_s(raw, 25, 27)),
        "race_name": name,
        "surface": surface,
        "distance": _int(_s(raw, 636, 640)),
        "going": None, "weather": None,
        "_post_time": None,
        "_field_size": _int(_s(raw, 652, 655)),
        "_grade": {"A": "G1", "B": "G2", "C": "G3", "D": "重賞", "L": "L",
                   "F": "J.G1", "G": "J.G2", "H": "J.G3"}.get(_s(raw, 614, 615)),
    }
    horses = []
    n = min(_int(_s(raw, 652, 655)) or 0, 300)
    for i in range(n):
        b = TK_HORSE_START + i * TK_HORSE_LEN
        if b + TK_HORSE_LEN > len(raw):
            break
        hid = _s(raw, b + 3, b + 13)
        if not hid:
            continue
        futan = _s(raw, b + 66, b + 69)
        horses.append({
            "num": _int(_s(raw, b, b + 3)) or i + 1,
            "horse_id": hid,
            "horse_name": _s(raw, b + 13, b + 49),
            "sex": SEX.get(_s(raw, b + 51, b + 52)),
            "trainer_id": _s(raw, b + 53, b + 58) or None,
            "trainer_name": _s(raw, b + 58, b + 66) or None,
            "kinryo": int(futan) / 10.0 if futan.isdigit() and int(futan) > 0 else None,
        })
    return info, horses


def cmd_toku(from_yyyymmdd, db_path):
    """特別登録馬 (TOKU) を取り込む。出馬表が出る前の週末の特別レース用。"""
    conn = open_db(db_path)
    jv = JVLink()
    jv.init()
    ret, read_count, dl_count = jv.open("TOKU", from_yyyymmdd + "000000", 1)
    if ret in (-1, -2):
        log("新しい特別登録はありません。")
        return
    n_race = n_horse = 0
    try:
        for spec, raw in jv.read_records():
            if spec != "TK":
                continue
            parsed = parse_tk(raw)
            if parsed is None:
                continue
            info, horses = parsed
            rid = info.pop("race_id")
            # 出馬表が既に入っているレースは races を上書きしない (発走時刻などが消えるため)
            have = conn.execute(
                "SELECT 1 FROM entries WHERE race_id=? LIMIT 1", (rid,)).fetchone()
            if not have:
                save_race_info(conn, rid, info)
            conn.execute("DELETE FROM tokubetsu WHERE race_id=?", (rid,))
            conn.executemany(
                "INSERT OR REPLACE INTO tokubetsu VALUES (?,?,?,?,?,?,?,?)",
                [(rid, h["num"], h["horse_id"], h["horse_name"], h["sex"],
                  h["trainer_id"], h["trainer_name"], h["kinryo"]) for h in horses])
            n_race += 1
            n_horse += len(horses)
        conn.commit()
    finally:
        jv.close()
        conn.close()
    log(f"特別登録 取り込み完了: {n_race}レース / {n_horse}頭")


def cmd_workout(from_yyyymmdd, db_path, setup=False):
    """坂路調教データ (HC) を取得する。"""
    conn = open_db(db_path)
    jv = JVLink()
    jv.init()
    ret, read_count, dl_count = jv.open(
        "SLOP", from_yyyymmdd + "000000", 4 if setup else 1
    )
    if ret in (-1, -2):
        log("新しい調教データはありません。")
        return
    log(f"調教データ ダウンロード開始 (対象 {read_count:,} ファイル)")
    n = 0
    try:
        for spec, raw in jv.read_records():
            if spec != "HC":
                continue
            w = parse_hc(raw)
            if w is None:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO workouts VALUES (?,?,?,?,?,?)",
                (w["horse_id"], w["date"], w["time4f"], w["time3f"],
                 w["time2f"], w["lap1f"]),
            )
            n += 1
            if n % 5000 == 0:
                conn.commit()
                log(f"調教取得中... {n:,}件")
    finally:
        conn.commit()
        jv.close()
        conn.close()
    log(f"JRA-VAN 調教取得 完了: {n:,}件")


def cmd_horse(from_yyyymmdd, db_path, setup=False):
    conn = open_db(db_path)
    jv = JVLink()
    jv.init()
    if setup:
        # 初回セットアップ: 馬マスタ一括取得。DIFN は DIFF より収録が新しく、
        # 現役の若い世代 (2022年以降生まれ) まで含まれる。
        ret, read_count, dl_count = jv.open("DIFN", from_yyyymmdd + "000000", 4)
    else:
        ret, read_count, dl_count = jv.open("DIFF", from_yyyymmdd + "000000", 1)
    if ret in (-1, -2):
        log("新しいデータはありません。初回は --setup でフルセットアップを実行してください。")
        return
    log(f"馬マスタ ダウンロード開始 (対象 {read_count:,} レコード)")
    n = 0
    try:
        for spec, raw in jv.read_records():
            if spec != "UM":
                continue
            um = parse_um(raw)
            if um is None:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO horses VALUES (?, ?, ?, ?, ?)",
                (um["horse_id"], um["sire_id"], um["sire_name"],
                 um["dam_sire_id"], um["dam_sire_name"]),
            )
            n += 1
            if n % 2000 == 0:
                conn.commit()
                log(f"血統取得中... {n:,}頭")
    finally:
        conn.commit()
        jv.close()
        conn.close()
    log(f"JRA-VAN 血統取得 完了: {n:,}頭")


def cmd_probe(from_yyyymmdd, limit=5):
    """DB に書かずに RA/SE を数件だけ解析して表示する (列位置の確認用)。"""
    jv = JVLink()
    jv.init()
    ret, read_count, _ = jv.open("RACE", from_yyyymmdd + "000000", 1)
    if ret in (-1, -2):
        log("新しいデータはありません。")
        return
    log(f"対象 {read_count:,} レコード。先頭から {limit} 件ずつ確認します。")
    n_ra = n_se = 0
    try:
        for spec, raw in jv.read_records():
            if spec == "RA" and n_ra < limit:
                info = parse_ra(raw)
                if info is None:
                    continue
                n_ra += 1
                log(f"[RA] {info['race_id']} {info['place']}{info['race_no']}R "
                    f"{info['surface']}{info['distance']}m len={len(raw)}")
                log(f"     前半3F={info['_pace_s3']} 前半4F={info['_pace_s4']} "
                    f"上り3F={info['_pace_l3']} 上り4F={info['_pace_l4']}")
                log(f"     ラップ={info['_lap']}")
            elif spec == "SE" and n_se < limit:
                row = parse_se(raw)
                if row is None:
                    continue
                n_se += 1
                log(f"[SE] {row['race_id']} {row['horse_no']}番 "
                    f"{row['horse_name']} 騎手={row['jockey_name']!r} "
                    f"調教師={row['trainer_name']!r}({row['trainer_id']!r})")
            if n_ra >= limit and n_se >= limit:
                break
    finally:
        jv.close()
    log("probe 終了 (DB には書き込んでいません)")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    cmd = args[0]
    db_path = str(DB_PATH)
    if cmd == "probe":
        if len(args) < 2:
            log("使い方: probe <YYYYMMDD>")
            sys.exit(1)
        cmd_probe(args[1])
    elif cmd == "settings":
        jv = JVLink()
        jv.settings()
        log("JV-Link 設定画面を閉じました。")
    elif cmd == "toku":
        if len(args) < 2 or len(args[1]) != 8 or not args[1].isdigit():
            log("開始日を YYYYMMDD で指定してください。例: toku 20261105")
            sys.exit(1)
        cmd_toku(args[1], db_path)
    elif cmd in ("race", "horse", "workout"):
        if len(args) < 2 or len(args[1]) != 8 or not args[1].isdigit():
            log("開始日を YYYYMMDD で指定してください。例: race 20240101")
            sys.exit(1)
        setup = "--setup" in args
        if cmd == "race":
            cmd_race(args[1], db_path, setup=setup)
        elif cmd == "workout":
            cmd_workout(args[1], db_path, setup=setup)
        else:
            cmd_horse(args[1], db_path, setup=setup)
    elif cmd in ("rtrace", "rtodds", "entries"):
        if len(args) < 3:
            log(f"使い方: {cmd} <race_id(12桁)> <YYYYMMDD>")
            sys.exit(1)
        if cmd == "rtrace":
            cmd_rtrace(args[1], args[2], db_path)
        elif cmd == "entries":
            cmd_entries(args[1], args[2])
        else:
            cmd_rtodds(args[1], args[2])
    elif cmd == "daylist":
        if len(args) < 2:
            log("使い方: daylist <YYYYMMDD>")
            sys.exit(1)
        cmd_daylist(args[1], db_path)
    else:
        log(f"不明なコマンド: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
