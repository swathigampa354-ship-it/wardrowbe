"""Local preview stack: stub AI provider + FastAPI + the built UI, one command.

    python3 -m tests.preview_stack              # UI :3000, API :8000, stub :8991
    UI_DIR=... PORT=... STORAGE_DIR=... python3 -m tests.preview_stack

Deliberately *not* part of the deployment: production runs `backend/entrypoint.sh`,
which starts uvicorn and `node server.js` without a stub provider. This exists so
the whole flow (upload -> tag -> outfits) can be clicked through with no API key and
no quota - the same stub the e2e suite drives, so "works here" and "works in CI"
mean the same thing.

UI_DIR must point at a Next standalone build with `.next/static`, `messages/` and
`public/` next to `server.js` - the same three extras the root Dockerfile copies, so
pointing UI_DIR at `frontend/.next/standalone` after a build is the usual invocation.
"""

import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

FRONTEND_DEFAULT = Path(__file__).resolve().parents[2] / "frontend"


def _free_port() -> int:
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    os.environ.setdefault("FAKE_MODE", "ok")
    stub_port = int(os.environ.get("STUB_PORT") or _free_port())
    api_port = int(os.environ.get("BACKEND_PORT", "8000"))
    ui_port = int(os.environ.get("PORT", "3000"))
    storage = Path(
        os.environ.get("STORAGE_DIR") or Path(__file__).resolve().parents[2] / ".preview-storage"
    )
    storage.mkdir(parents=True, exist_ok=True)

    ui_dir = Path(os.environ.get("UI_DIR") or FRONTEND_DEFAULT / ".next" / "standalone")
    # Copy the three extras Next's standalone trace leaves out, only for a build directory
    # that sits next to its own source tree (i.e. frontend/.next/standalone). The Dockerfile
    # does the same thing explicitly; this only saves a manual step when previewing.
    for extra in (".next/static", "messages", "public"):
        src, dst = FRONTEND_DEFAULT / extra, ui_dir / extra
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["cp", "-r", str(src), str(dst)], check=False)
    missing = [e for e in (".next/static", "messages", "public") if not (ui_dir / e).exists()]
    if missing and (ui_dir / "server.js").exists():
        print(f"[preview] warning: {ui_dir} is missing {', '.join(missing)} - pages may 500")

    from tests import fake_provider

    threading.Thread(target=fake_provider.serve, args=(stub_port,), daemon=True).start()
    time.sleep(0.4)
    print(f"[preview] stub AI provider      http://127.0.0.1:{stub_port}/v1", flush=True)

    env = dict(
        os.environ,
        STORAGE_DIR=str(storage),
        AI_BASE_URL=f"http://127.0.0.1:{stub_port}/v1",
        AI_API_KEY="***",
        AI_VISION_MODEL="fake-vision",
        AI_TEXT_MODEL="fake-text",
        WEATHER_ENABLED=os.environ.get("WEATHER_ENABLED", "false"),
        DEBUG="false",
    )

    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(api_port),
            "--log-level",
            "warning",
        ],
        env=env,
        cwd=Path(__file__).resolve().parents[1],
    )
    print(f"[preview] API                     http://127.0.0.1:{api_port}", flush=True)

    ui = None
    if (ui_dir / "server.js").exists():
        ui = subprocess.Popen(
            ["node", "server.js"],
            cwd=ui_dir,
            env=dict(
                env,
                HOSTNAME="0.0.0.0",
                PORT=str(ui_port),
                BACKEND_URL=f"http://127.0.0.1:{api_port}",
            ),
        )
        print(f"[preview] UI (built)              http://127.0.0.1:{ui_port}", flush=True)
    else:
        print(
            f"[preview] no server.js in {ui_dir} - API only. Run `npm run build` in frontend/ "
            "or set UI_DIR (dev server: `npm run dev`).",
            flush=True,
        )

    try:
        api.wait()
        return 0
    finally:
        for child in (ui,):
            if child:
                child.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
