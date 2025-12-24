import os
import sys
import time
import signal
import warnings
import subprocess
import platform
from dotenv import load_dotenv

load_dotenv()

# Quiet noisy libs
warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")
warnings.filterwarnings("ignore", category=UserWarning, module="pandas_ta")
warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API",
    category=UserWarning,
    module=r"pandas_ta\.__init__",
)
os.environ.setdefault("PYTHONWARNINGS", "ignore:::pandas_ta.__init__")


def launch(cmd, name):
    print(f"🚀 Starting {name}...")
    return subprocess.Popen(
        cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=os.environ.copy(),
    )


def shutdown_process(p):
    if not p or p.poll() is not None:
        return

    try:
        if platform.system() == "Windows":
            p.terminate()
        else:
            p.send_signal(signal.SIGINT)
    except Exception:
        p.kill()


if __name__ == "__main__":
    backend_host = os.environ.get("APP_HOST", "127.0.0.1")
    backend_port = os.environ.get("APP_PORT", "8000")

    dt_host = os.environ.get("DT_APP_HOST", "127.0.0.1")
    dt_port = os.environ.get("DT_APP_PORT", "8010")

    replay_host = os.environ.get("REPLAY_APP_HOST", "127.0.0.1")
    replay_port = os.environ.get("REPLAY_APP_PORT", "8020")

    print("────────────────────────────────────────")
    print("🚀 Launching AION Analytics system")
    print(f"   • backend          → http://{backend_host}:{backend_port}")
    print(f"   • dt_backend       → http://{dt_host}:{dt_port}")
    print(f"   • replay_service   → http://{replay_host}:{replay_port} (dormant)")
    print("────────────────────────────────────────", flush=True)

    backend_proc = launch(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.backend_service:app",
            "--host",
            backend_host,
            "--port",
            backend_port,
        ],
        "backend",
    )

    dt_proc = launch(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "dt_backend.fastapi_main:app",
            "--host",
            dt_host,
            "--port",
            dt_port,
        ],
        "dt_backend",
    )

    dt_live_proc = launch(
        [
            sys.executable,
            "-m",
            "dt_backend.jobs.live_market_data_loop",
        ],
        "dt_backend_live_loop",
    )

    replay_proc = launch(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "replay_service:app",
            "--host",
            replay_host,
            "--port",
            replay_port,
        ],
        "replay_service",
    )

    try:
        while True:
            time.sleep(1)

            if backend_proc.poll() is not None:
                raise RuntimeError("backend process exited")

            if dt_proc.poll() is not None:
                raise RuntimeError("dt_backend API process exited")

            if dt_live_proc.poll() is not None:
                raise RuntimeError("dt_backend live loop exited")

            if replay_proc.poll() is not None:
                raise RuntimeError("replay service exited")

    except KeyboardInterrupt:
        print("\n🛑 Shutting down AION system...")

    finally:
        shutdown_process(replay_proc)
        shutdown_process(dt_live_proc)
        shutdown_process(dt_proc)
        shutdown_process(backend_proc)
        time.sleep(2)
