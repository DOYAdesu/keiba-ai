"""どのディレクトリからでもサーバを起動できるようにする薄いランチャ。

    python 競馬予想/web/run.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.server import main  # noqa: E402

if __name__ == "__main__":
    # python web/run.py --port 8010 のようにポートを変えられる
    port = sys.argv[sys.argv.index("--port") + 1] if "--port" in sys.argv else None
    main(port)
