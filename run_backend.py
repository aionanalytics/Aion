import os
import sys
import time
import signal
import warnings
import subprocess
import platform
import threading
from typing import Optional

import importlib.util


def module_exists(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


# dotenv is optional; if .env has bad lines we don't want to crash boot
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---------------------------------------------------------------------------
# Optional knobs env files (kept separate from API keys)
# ---------------------------------------------------------------------------
try:
    from pathlib import Path as _Path

    _KNOBS_ENV = (os.getenv("KNOBS_ENV_PATH", "") or "").strip()
    _DT_KNOBS_ENV = (os.getenv("DT_KNOBS_ENV_PATH", "") or "").strip()

    _here = _Path(__file__).resolve().parent
    _knobs_path = _Path(_KNOBS_ENV) if _KNOBS_ENV else (_here / "knobs.env")
    _dt_knobs_path = _Path(_DT_KNOBS_ENV) if _DT_KNOBS_ENV else (_here / "dt_knobs.env")

    if _knobs_path.exists():
        load_dotenv(dotenv_path=str(_knobs_path), override=False)
    if _dt_knobs_path.exists():
        load_dotenv(dotenv_path=str(_dt_knobs_path), override=False)
except Exception:
    pass

from utils.live_log import append_log, prune_old_logs
from backend.core.config import PATHS

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


def _is_valid_bind_host(host: str) -> bool:
    h = (host or "").strip().lower()
    if not h:
        return False
    if h == "0.0.0.1":
        return False
    return True


def _normalize_bind_host(host: str, default: str = "0.0.0.0") -> str:
    return host if _is_valid_bind_host(host) else default


def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.environ.get(name, "") or "").strip().lower()
    if v == "":
        return default
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    if raw == "":
        return int(default)
    try:
        return int(float(raw))
    except Exception:
        return int(default)


def uvicorn_cmd(
    app: str,
    host: str,
    port: str | int,
    *,
    log_level: str = "warning",
    access_log: bool = False,
    reload: bool = False,
    workers: int = 1,
    ssl_cert: Optional[str] = None,
    ssl_key: Optional[str] = None,
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        app,
        "--host",
        str(host),
        "--port",
        str(port),
        "--log-level",
        str(log_level),
    ]

    if not access_log:
        cmd.append("--no-access-log")

    if reload:
        cmd.append("--reload")

    try:
        w = int(workers)
        if w > 1 and not reload:
            cmd += ["--workers", str(w)]
    except Exception:
        pass

    # Add SSL/TLS support if certificates are provided
    if ssl_cert and ssl_key:
        cmd += ["--ssl-certfile", str(ssl_cert)]
        cmd += ["--ssl-keyfile", str(ssl_key)]

    return cmd


def launch(cmd: list[str], name: str, env_overrides: Optional[dict[str, str]] = None) -> subprocess.Popen:
    print(f"🚀 Starting {name}...")
    append_log(f"Starting {name}")
    prune_old_logs()

    env = os.environ.copy()
    if env_overrides:
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[str(k)] = str(v)

    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )


def _should_silence_supervisor_agent() -> bool:
    return _env_bool("SILENCE_SUPERVISOR_AGENT", default=True)


def pipe_output(proc: subprocess.Popen, name: str):
    if proc.stdout is None:
        return

    silence_supervisor = _should_silence_supervisor_agent()

    for line in proc.stdout:
        if silence_supervisor and ("[supervisor_agent]" in line or "supervisor_agent" in line):
            continue

        try:
            print(line, end="")
        except Exception:
            pass

        append_log(f"[{name}] {line}")


def shutdown_process(p: Optional[subprocess.Popen], name: str = ""):
    if not p or p.poll() is not None:
        return

    try:
        append_log(f"Stopping {name or 'process'} (pid={p.pid})")
    except Exception:
        pass

    try:
        if platform.system() == "Windows":
            p.terminate()
        else:
            p.send_signal(signal.SIGINT)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


if __name__ == "__main__":
    backend_host = _normalize_bind_host(os.environ.get("APP_HOST", "0.0.0.0"))
    primary_port = os.environ.get("APP_PORT", "8000")  # ✅ unified port for all backend endpoints

    dt_host = _normalize_bind_host(os.environ.get("DT_APP_HOST", "0.0.0.0"))
    dt_port = os.environ.get("DT_APP_PORT", "8010")

    replay_host = _normalize_bind_host(os.environ.get("REPLAY_APP_HOST", "0.0.0.0"))
    replay_port = os.environ.get("REPLAY_APP_PORT", "8020")

    UVICORN_LOG_LEVEL = os.environ.get("UVICORN_LOG_LEVEL", "warning").strip() or "warning"
    UVICORN_ACCESS_LOG = _env_bool("UVICORN_ACCESS_LOG", default=False)
    UVICORN_RELOAD = _env_bool("UVICORN_RELOAD", default=False)

    # ✅ Single backend process with multiple workers for performance
    PRIMARY_WORKERS = _env_int("APP_WORKERS", 1)

    DT_WORKERS = _env_int("DT_APP_WORKERS", 1)
    REPLAY_WORKERS = _env_int("REPLAY_APP_WORKERS", 1)

    # SSL/TLS Configuration
    # Auto-detect SSL certificates or use environment variables
    from pathlib import Path as _Path
    _here = _Path(__file__).resolve().parent
    _default_cert = _here / "ssl" / "cert.pem"
    _default_key = _here / "ssl" / "key.pem"
    
    SSL_ENABLED = _env_bool("SSL_ENABLED", default=_default_cert.exists() and _default_key.exists())
    SSL_CERT_FILE = os.environ.get("SSL_CERT_FILE", str(_default_cert) if _default_cert.exists() else "")
    SSL_KEY_FILE = os.environ.get("SSL_KEY_FILE", str(_default_key) if _default_key.exists() else "")
    
    # Validate SSL files exist if SSL is enabled
    ssl_cert = None
    ssl_key = None
    protocol = "http"
    
    if SSL_ENABLED:
        if _Path(SSL_CERT_FILE).exists() and _Path(SSL_KEY_FILE).exists():
            ssl_cert = SSL_CERT_FILE
            ssl_key = SSL_KEY_FILE
            protocol = "https"
            print("🔐 SSL/TLS enabled - using HTTPS")
        else:
            print(f"⚠️  SSL enabled but certificates not found:")
            print(f"   - cert: {SSL_CERT_FILE}")
            print(f"   - key: {SSL_KEY_FILE}")
            print("   Run ./generate_ssl_cert.sh to generate certificates")
            print("   Falling back to HTTP...")

    print("────────────────────────────────────────")
    print("🚀 Launching AION Analytics system")
    print(f"   • backend API       → {protocol}://{backend_host}:{primary_port} (workers={PRIMARY_WORKERS}, unified port)")
    print(f"   • dt_backend        → {protocol}://{dt_host}:{dt_port} (workers={DT_WORKERS})")
    print(f"   • replay_service    → {protocol}://{replay_host}:{replay_port} (dormant, workers={REPLAY_WORKERS})")
    if _should_silence_supervisor_agent():
        print("   • logs              → supervisor_agent SILENCED (set SILENCE_SUPERVISOR_AGENT=0 to show)")
    print("────────────────────────────────────────", flush=True)

    # Validate configuration before launching services (PR #6: Config Validation)
    # Fail-safe: startup continues even if validation fails
    try:
        from dt_backend.core.knob_validator_dt import validate_and_warn
        validate_and_warn()
    except (ImportError, ModuleNotFoundError) as e:
        print(f"⚠️  Config validator not available: {e}")
    except Exception as e:
        # Catch-all for any unexpected errors during validation
        # This is intentionally broad to ensure startup continues
        print(f"⚠️  Configuration validation error: {e}")

    append_log("AION system starting")
    prune_old_logs()

    # ✅ Unified backend process (single port, multiple workers)
    backend_primary_proc = launch(
        uvicorn_cmd(
            "backend.backend_service:app",
            backend_host,
            primary_port,
            log_level=UVICORN_LOG_LEVEL,
            access_log=UVICORN_ACCESS_LOG,
            reload=UVICORN_RELOAD,
            workers=PRIMARY_WORKERS,
            ssl_cert=ssl_cert,
            ssl_key=ssl_key,
        ),
        "backend",
        env_overrides={
            "AION_ROLE": "primary",
            "QUIET_STARTUP": "0",
            "ENABLE_SCHEDULER": "1",
            "ENABLE_HEARTBEAT": "1",
            "ENABLE_CLOUDSYNC": "1",
            "PRIMARY_BACKEND_URL": f"{protocol}://127.0.0.1:{primary_port}",
        },
    )
    threading.Thread(target=pipe_output, args=(backend_primary_proc, "backend"), daemon=True).start()

    # DT backend API
    dt_proc = launch(
        uvicorn_cmd(
            "dt_backend.fastapi_main:app",
            dt_host,
            dt_port,
            log_level=UVICORN_LOG_LEVEL,
            access_log=UVICORN_ACCESS_LOG,
            reload=UVICORN_RELOAD,
            workers=DT_WORKERS,
            ssl_cert=ssl_cert,
            ssl_key=ssl_key,
        ),
        "dt_backend",
    )
    threading.Thread(target=pipe_output, args=(dt_proc, "dt_backend"), daemon=True).start()

    # DT worker: prefer dt_scheduler if present; otherwise fall back to the legacy live loop.
    dt_worker_module = (
        "dt_backend.jobs.dt_scheduler"
        if module_exists("dt_backend.jobs.dt_scheduler")
        else "dt_backend.jobs.live_market_data_loop"
    )
    dt_worker_name = "dt_scheduler" if dt_worker_module.endswith("dt_scheduler") else "dt_backend_live_loop"
    dt_worker_proc = launch([sys.executable, "-m", dt_worker_module], dt_worker_name)
    threading.Thread(target=pipe_output, args=(dt_worker_proc, dt_worker_name), daemon=True).start()

    # Backend scheduler: run scheduled jobs (nightly_job, news_fetcher, etc.)
    backend_scheduler_proc = None
    if module_exists("backend.scheduler_runner"):
        backend_scheduler_proc = launch([sys.executable, "-m", "backend.scheduler_runner"], "backend_scheduler")
        threading.Thread(target=pipe_output, args=(backend_scheduler_proc, "backend_scheduler"), daemon=True).start()
        print("   ✓ backend scheduler → running scheduled jobs (nightly_job at 17:30 MT)", flush=True)
    else:
        print("   ⚠️ backend scheduler → module not found, skipping", flush=True)

    replay_proc = launch(
        uvicorn_cmd(
            "replay_service:app",
            replay_host,
            replay_port,
            log_level=UVICORN_LOG_LEVEL,
            access_log=UVICORN_ACCESS_LOG,
            reload=UVICORN_RELOAD,
            workers=REPLAY_WORKERS,
            ssl_cert=ssl_cert,
            ssl_key=ssl_key,
        ),
        "replay_service",
    )
    threading.Thread(target=pipe_output, args=(replay_proc, "replay_service"), daemon=True).start()

    try:
        while True:
            if backend_primary_proc.poll() is not None:
                raise RuntimeError("backend process exited")

            if dt_proc.poll() is not None:
                raise RuntimeError("dt_backend (API) process exited")

            if dt_worker_proc.poll() is not None:
                rc = dt_worker_proc.returncode
                msg = f"[supervisor] ⚠️ {dt_worker_name} exited (code={rc}) — restarting…"
                print(msg, flush=True)
                append_log(msg)
                time.sleep(3)
                dt_worker_proc = launch([sys.executable, "-m", dt_worker_module], dt_worker_name)
                threading.Thread(target=pipe_output, args=(dt_worker_proc, dt_worker_name), daemon=True).start()

            if backend_scheduler_proc is not None and backend_scheduler_proc.poll() is not None:
                rc = backend_scheduler_proc.returncode
                msg = f"[supervisor] ⚠️ backend_scheduler exited (code={rc}) — restarting…"
                print(msg, flush=True)
                append_log(msg)
                time.sleep(3)
                backend_scheduler_proc = launch([sys.executable, "-m", "backend.scheduler_runner"], "backend_scheduler")
                threading.Thread(target=pipe_output, args=(backend_scheduler_proc, "backend_scheduler"), daemon=True).start()

            if replay_proc is not None and replay_proc.poll() is not None:
                rc = replay_proc.returncode
                msg = f"[supervisor] ⚠️ replay_service exited (code={rc}) — continuing without it"
                print(msg, flush=True)
                append_log(msg)
                replay_proc = None

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n🛑 Shutting down AION system...")
        append_log("AION system shutdown requested (KeyboardInterrupt)")

    finally:
        shutdown_process(replay_proc, "replay_service")
        shutdown_process(backend_scheduler_proc, "backend_scheduler")
        shutdown_process(dt_worker_proc, dt_worker_name)
        shutdown_process(dt_proc, "dt_backend")
        shutdown_process(backend_primary_proc, "backend")
        time.sleep(2)
        append_log("AION system stopped")
