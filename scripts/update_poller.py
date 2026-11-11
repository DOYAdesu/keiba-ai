"""サイトの「更新」ボタンの依頼を受けて、この PC で取り込み・更新を走らせる。

サイト (Cloudflare) の /api/update に置かれた依頼を30秒ごとに見に行き、依頼があれば
scripts/manual_update.ps1 (出馬表だけ / 全部) を実行して、結果をサイトへ返す。
更新キーは data/update_token.txt (Cloudflare 側は Pages のシークレット UPDATE_TOKEN)。

  python scripts/update_poller.py      # 「更新ボタンの受付.bat」から起動する
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("KEIBA_UPDATE_API", "https://keiba-ai-3ex.pages.dev/api/update")
TOKEN_FILE = ROOT / "data" / "update_token.txt"
LOG = ROOT / "data" / "update_poller.log"
PID = ROOT / "data" / "update_poller.pid"
POLL_SEC = 30
BEAT_SEC = 10 * 60
UA = "keiba-update-poller/1.0"


def log(msg):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def call(token, method, sub="", body=None):
    url = API + (f"/{sub}" if sub else "")
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={
        "authorization": f"Bearer {token}", "content-type": "application/json", "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def already_running():
    """二重起動を防ぐ (前の受付がまだ生きていれば降りる)。"""
    if PID.exists():
        try:
            pid = int(PID.read_text().strip())
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                                 text=True).stdout
            if str(pid) in out and "python" in out.lower():
                return True
        except (ValueError, OSError):
            pass
    PID.write_text(str(os.getpid()))
    return False


def run_update(mode):
    """manual_update.ps1 を走らせ、(成功か, ひとことの結果) を返す。"""
    p = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts" / "manual_update.ps1"), "-Mode", mode],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines = [l.strip() for l in (p.stdout or "").splitlines() if l.strip().startswith("[")]
    keys = ("失敗", "取り込めません", "動いています", "完了: 1分ほど", "終わりました")
    notes = [l.split("] ", 1)[-1].strip("= ").replace(" https://keiba-ai-3ex.pages.dev", "")
             for l in lines if any(k in l for k in keys)]
    if any("取り込めません" in n for n in notes):  # 同じことを2回言わない
        notes = [n for n in notes if not n.startswith("失敗: JRA-VAN")]
    ok = p.returncode == 0 and not any(n.startswith("失敗: これから") or n.startswith("失敗: サイト")
                                       or n.startswith("失敗: Cloudflare") for n in notes)
    return ok, " / ".join(notes[-4:]) or f"終了コード {p.returncode}"


def main():
    if already_running():
        print("更新ボタンの受付はもう動いています")
        return
    if not TOKEN_FILE.exists() and not os.environ.get("KEIBA_UPDATE_TOKEN"):
        log(f"更新キーのファイルがありません: {TOKEN_FILE}")
        sys.exit(1)
    token = os.environ.get("KEIBA_UPDATE_TOKEN") or TOKEN_FILE.read_text(encoding="utf-8").strip()
    log(f"受付を始めました ({API}、{POLL_SEC}秒ごとに確認)")
    last_beat = 0.0
    warned = False
    while True:
        try:
            if time.time() - last_beat > BEAT_SEC:
                st, _ = call(token, "POST", "beat")
                if st == 401 and not warned:
                    log("サイトが更新キーを受け付けません (Cloudflare のシークレット UPDATE_TOKEN が未設定か違う)")
                    warned = True
                elif st == 200:
                    warned = False
                last_beat = time.time()
            st, state = call(token, "GET", "poll")
            if st == 200 and state.get("status") == "pending":
                st, res = call(token, "POST", "claim")
                job = res.get("job") if st == 200 else None
                if job:
                    mode = job.get("mode", "quick")
                    log(f"依頼を受けました ({'出馬表だけ' if mode == 'quick' else '全部'}、依頼 {job.get('requested_at')})")
                    ok, msg = run_update(mode)
                    log(f"{'完了' if ok else '失敗'}: {msg}")
                    call(token, "POST", "done", {"ok": ok, "message": msg})
                    last_beat = time.time()
        except Exception as e:  # ネットが切れていても受付は止めない
            log(f"確認に失敗: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
