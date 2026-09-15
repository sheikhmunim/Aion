"""Auto-install Ollama and pull a model for smart intent classification."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time

import httpx
from rich.progress import BarColumn, DownloadColumn, Progress, TransferSpeedColumn

from aion.config import get_config, save_config
from aion.display import console

DEFAULT_MODEL = "qwen2.5:3b"

# winget/brew/apt installs are metadata-driven and usually fast, but can stall
# on slow mirrors. The Ollama installer itself is a large (~700MB+) download,
# so it gets a longer allowance below.
INSTALL_TIMEOUT = 900  # 15 min
DOWNLOAD_TIMEOUT = 900  # 15 min
MODEL_PULL_TIMEOUT = 1800  # 30 min, model can be several GB


def _is_ollama_installed() -> bool:
    """Check if the ollama binary is on PATH."""
    return shutil.which("ollama") is not None


def _is_ollama_running() -> bool:
    """Check if the Ollama server is responding."""
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        return r.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def _has_model(model: str) -> bool:
    """Check if a specific model is already pulled."""
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
        if r.status_code == 200:
            models = r.json().get("models", [])
            return any(m.get("name", "").startswith(model.split(":")[0]) for m in models)
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return False


def _list_installed_models() -> list[str]:
    """Return the names of models already pulled into Ollama, if any."""
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", []) if m.get("name")]
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


def _run_with_status(args: list[str], message: str, timeout: float) -> subprocess.CompletedProcess | None:
    """Run a subprocess behind a live spinner. Returns None on timeout/failure to launch."""
    with console.status(f"[bold cyan]{message}", spinner="dots"):
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            console.print(f"  [yellow]Timed out after {int(timeout)}s: {message}[/]")
            return None
        except FileNotFoundError:
            return None


def _download_with_progress(url: str, dest: str, timeout: float) -> bool:
    """Download a file to dest with a live progress bar. Returns True on success."""
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0)) or None
                with Progress(
                    "[progress.description]{task.description}",
                    BarColumn(),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("  Downloading Ollama installer", total=total)
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_bytes(8192):
                            f.write(chunk)
                            progress.update(task, advance=len(chunk))
        return True
    except (httpx.HTTPError, OSError) as e:
        console.print(f"  [yellow]Download failed: {e}[/]")
        return False


def install_ollama() -> bool:
    """Install Ollama. Returns True on success. Never raises."""
    try:
        system = platform.system()

        if system == "Windows":
            if shutil.which("winget"):
                result = _run_with_status(
                    ["winget", "install", "Ollama.Ollama", "--silent",
                     "--accept-package-agreements", "--accept-source-agreements"],
                    "Installing Ollama via winget...",
                    INSTALL_TIMEOUT,
                )
                if result is not None and result.returncode == 0:
                    return True
                # winget may return non-zero (or time out) even when it already succeeded
                if _is_ollama_installed():
                    return True

            # Fallback: direct download
            installer_path = "OllamaSetup.exe"
            if not _download_with_progress("https://ollama.com/download/OllamaSetup.exe", installer_path, DOWNLOAD_TIMEOUT):
                return False
            result = _run_with_status(
                [installer_path, "/VERYSILENT", "/NORESTART"],
                "Running Ollama installer...",
                INSTALL_TIMEOUT,
            )
            return result is not None and (result.returncode == 0 or _is_ollama_installed())

        elif system == "Darwin":  # macOS
            if shutil.which("brew"):
                result = _run_with_status(["brew", "install", "ollama"], "Installing Ollama via Homebrew...", INSTALL_TIMEOUT)
                return result is not None and (result.returncode == 0 or _is_ollama_installed())

        elif system == "Linux":
            result = _run_with_status(
                ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
                "Installing Ollama...",
                INSTALL_TIMEOUT,
            )
            return result is not None and (result.returncode == 0 or _is_ollama_installed())

        return False
    except Exception as e:
        console.print(f"  [yellow]Ollama install failed: {e}[/]")
        return False


SERVER_START_TIMEOUT = 30  # seconds — first cold start can be slow (AV scan, disk, etc.)


def start_ollama() -> bool:
    """Start the Ollama server if not running."""
    if _is_ollama_running():
        return True

    ollama_path = shutil.which("ollama")
    if not ollama_path:
        return False

    # Start ollama serve in background
    if platform.system() == "Windows":
        subprocess.Popen(
            [ollama_path, "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        )
    else:
        subprocess.Popen(
            [ollama_path, "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    # Wait for server to start. A cold start (first launch, AV scanning the
    # binary, slow disk) can take much longer than a few seconds — poll
    # patiently rather than giving up while it's still on its way up.
    with console.status("[bold cyan]Waiting for Ollama server to start...", spinner="dots"):
        deadline = time.monotonic() + SERVER_START_TIMEOUT
        while time.monotonic() < deadline:
            if _is_ollama_running():
                return True
            time.sleep(0.5)
    return False


def pull_model(model: str = DEFAULT_MODEL) -> bool:
    """Pull a model. Shows Ollama's own live progress output."""
    print(f"  Downloading model '{model}' (this may take a few minutes)...")
    try:
        result = subprocess.run(
            ["ollama", "pull", model],
            timeout=MODEL_PULL_TIMEOUT,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        console.print(f"  [yellow]Timed out after {MODEL_PULL_TIMEOUT}s downloading model '{model}'.[/]")
        return False
    except FileNotFoundError:
        return False


def _resolve_model(explicit_model: str | None, cfg: dict) -> str:
    """Pick which model to use.

    Priority: an explicitly requested model (arg or AION_OLLAMA_MODEL env var)
    always wins. Otherwise, if a previous run of `setup()` already completed
    and recorded a choice, stick with it. Otherwise (first-ever setup), prefer
    a model the user already has pulled over downloading the default fresh.
    """
    if explicit_model:
        return explicit_model
    if os.environ.get("AION_OLLAMA_MODEL"):
        return os.environ["AION_OLLAMA_MODEL"]
    if cfg.get("ollama_setup_done"):
        return cfg.get("ollama_model", DEFAULT_MODEL)

    installed = _list_installed_models()
    if any(m.startswith(DEFAULT_MODEL.split(":")[0]) for m in installed):
        return DEFAULT_MODEL
    if installed:
        return installed[0]
    return DEFAULT_MODEL


def setup(model: str | None = None) -> bool:
    """Full setup: install Ollama, start server, pull model. Returns True on success. Never raises."""
    try:
        cfg = get_config()

        # Step 1: Install if needed
        if not _is_ollama_installed():
            print("\n  Setting up smart command understanding...")
            if not install_ollama():
                print("  Could not install Ollama automatically.")
                print("  Install manually from: https://ollama.com/download")
                return False
            print("  Ollama installed!")

        # Step 2: Start server
        if not start_ollama():
            print("  Could not start Ollama server.")
            return False

        # Step 3: Pick a model (reuse one already pulled unless something more specific was requested)
        target_model = _resolve_model(model, cfg)
        if target_model != DEFAULT_MODEL and not (model or os.environ.get("AION_OLLAMA_MODEL")):
            print(f"  Found existing model '{target_model}' — using it instead of downloading the default.")

        # Step 4: Pull model if needed
        if not _has_model(target_model):
            if not pull_model(target_model):
                print(f"  Could not download model '{target_model}'.")
                return False
            print(f"  Model '{target_model}' ready!")

        # Save to config
        cfg["ollama_model"] = target_model
        cfg["ollama_enabled"] = True
        cfg["ollama_setup_done"] = True
        save_config(cfg)

        return True
    except Exception as e:
        console.print(f"  [yellow]Setup failed unexpectedly: {e}[/]")
        return False
