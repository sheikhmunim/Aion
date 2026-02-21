"""Auto-install Ollama and pull a model for smart intent classification."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time

import httpx

from aion.config import get_config, save_config

DEFAULT_MODEL = "qwen2.5:3b"


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


def install_ollama() -> bool:
    """Install Ollama. Returns True on success."""
    system = platform.system()

    if system == "Windows":
        # Try winget first
        if shutil.which("winget"):
            print("  Installing Ollama via winget...")
            result = subprocess.run(
                ["winget", "install", "Ollama.Ollama", "--silent", "--accept-package-agreements", "--accept-source-agreements"],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                return True
            # winget may return non-zero even on success (already installed, etc.)
            if _is_ollama_installed():
                return True

        # Fallback: direct download
        print("  Downloading Ollama installer...")
        url = "https://ollama.com/download/OllamaSetup.exe"
        installer_path = "OllamaSetup.exe"
        try:
            with httpx.Client(follow_redirects=True, timeout=120.0) as client:
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(installer_path, "wb") as f:
                        for chunk in resp.iter_bytes(8192):
                            f.write(chunk)
            print("  Running installer (this may take a moment)...")
            subprocess.run([installer_path, "/VERYSILENT", "/NORESTART"], timeout=300)
            return _is_ollama_installed()
        except Exception as e:
            print(f"  Download failed: {e}")
            return False

    elif system == "Darwin":  # macOS
        if shutil.which("brew"):
            print("  Installing Ollama via Homebrew...")
            result = subprocess.run(["brew", "install", "ollama"], capture_output=True, text=True, timeout=300)
            return result.returncode == 0 or _is_ollama_installed()

    elif system == "Linux":
        print("  Installing Ollama...")
        result = subprocess.run(
            ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
            capture_output=True, text=True, timeout=300,
        )
        return result.returncode == 0 or _is_ollama_installed()

    return False


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

    # Wait for server to start
    for _ in range(20):
        time.sleep(0.5)
        if _is_ollama_running():
            return True
    return False


def pull_model(model: str = DEFAULT_MODEL) -> bool:
    """Pull a model. Shows progress."""
    print(f"  Downloading model '{model}' (this may take a few minutes)...")
    try:
        result = subprocess.run(
            ["ollama", "pull", model],
            timeout=600,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def setup(model: str | None = None) -> bool:
    """Full setup: install Ollama, start server, pull model. Returns True on success."""
    model = model or get_config().get("ollama_model", DEFAULT_MODEL)

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

    # Step 3: Pull model if needed
    if not _has_model(model):
        if not pull_model(model):
            print(f"  Could not download model '{model}'.")
            return False
        print(f"  Model '{model}' ready!")

    # Save to config
    cfg = get_config()
    cfg["ollama_model"] = model
    cfg["ollama_enabled"] = True
    save_config(cfg)

    return True
