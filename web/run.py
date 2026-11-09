"""どのディレクトリからでもサーバを起動できるようにする薄いランチャ。

    python 競馬予想/web/run.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.server import main  # noqa: E402

if __name__ == "__main__":
    main()
