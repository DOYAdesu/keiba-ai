"""書き出したサイトをローカルで確認するためのサーバ。

Cloudflare Pages に置くのと同じ dist/ をそのまま配るので、
手元で見えているものが本番と一致する。

    .venv\\Scripts\\python -m web.build     # 予想を事前計算
    .venv\\Scripts\\python -m web.export    # dist/ に書き出し
    .venv\\Scripts\\python -m web.server    # http://127.0.0.1:8000
"""
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from web.export import DIST

app = FastAPI(title="競馬予想AI")


@app.middleware("http")
async def no_cache(request, call_next):
    """直したのに古いままになるのを防ぐ (ローカル確認用なので常に無効化)。"""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


if DIST.exists():
    # html=True で / を index.html に向ける
    app.mount("/", StaticFiles(directory=DIST, html=True), name="site")
else:
    @app.get("/{path:path}")
    def not_built(path: str):
        return PlainTextResponse(
            "dist/ がありません。先に次を実行してください:\n"
            "  python -m web.build\n"
            "  python -m web.export\n",
            status_code=503,
        )


def main():
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
