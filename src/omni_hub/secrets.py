from __future__ import annotations

import os
import platform
import subprocess
import json
from pathlib import Path
from tempfile import NamedTemporaryFile


KEYCHAIN_PREFIX = "omni-hub"
_MEMORY_SECRETS: dict[str, str] = {}


class SecretStoreError(RuntimeError):
    pass


def store_api_key(account_id: str, api_key: str) -> str:
    key = api_key.strip()
    if not key:
        raise SecretStoreError("api key is empty")
    target = _target(account_id)
    backend = _backend()
    if backend == "memory":
        _MEMORY_SECRETS[target] = key
        return f"local:{target}"
    if backend in {"local", "file"}:
        _write_local_secret(target, key)
        return f"local:{target}"
    raise SecretStoreError(f"unsupported secret backend: {backend}")


def resolve_secret_ref(secret_ref: str) -> str:
    ref = secret_ref.strip()
    if not ref:
        return ""
    prefix, _, value = ref.partition(":")
    if prefix == "env":
        return os.environ.get(value, "")
    if prefix == "runtime":
        return _MEMORY_SECRETS.get(value, "")
    if prefix == "local":
        if _backend() == "memory":
            return _MEMORY_SECRETS.get(value, "")
        return _read_local_secret(value)
    if prefix == "keychain":
        if _backend() == "memory":
            return _MEMORY_SECRETS.get(value, "")
        if platform.system() != "Darwin":
            raise SecretStoreError("macOS Keychain is required to resolve keychain refs")
        result = _run_security(["find-generic-password", "-s", value, "-w"])
        return result.stdout.strip()
    return ""


def has_secret(secret_ref: str) -> bool:
    try:
        return bool(resolve_secret_ref(secret_ref))
    except SecretStoreError:
        raise
    except Exception as exc:
        raise SecretStoreError(str(exc)) from exc


def _target(account_id: str) -> str:
    return f"{KEYCHAIN_PREFIX}/{account_id.strip()}"


def _backend() -> str:
    return os.environ.get("OMNI_HUB_SECRET_BACKEND", "local").strip().lower()


def _local_secret_path() -> Path:
    configured = os.environ.get("OMNI_HUB_SECRET_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    base = os.environ.get("OMNI_HUB_HOME", "").strip()
    root = Path(base).expanduser() if base else Path.cwd() / ".omni"
    return root / "secrets.json"


def _read_local_secrets() -> dict[str, str]:
    path = _local_secret_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SecretStoreError(f"failed to read local secret file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SecretStoreError(f"failed to parse local secret file: {exc}") from exc
    secrets = data.get("secrets", data) if isinstance(data, dict) else {}
    if not isinstance(secrets, dict):
        raise SecretStoreError("local secret file has invalid format")
    return {str(key): str(value) for key, value in secrets.items()}


def _write_local_secret(target: str, value: str) -> None:
    path = _local_secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    secrets = _read_local_secrets()
    secrets[target] = value
    payload = {"version": 1, "secrets": secrets}
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temp_name = handle.name
        Path(temp_name).replace(path)
        path.chmod(0o600)
    except OSError as exc:
        raise SecretStoreError(f"failed to write local secret file: {exc}") from exc


def _read_local_secret(target: str) -> str:
    return _read_local_secrets().get(target, "")


def _run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["security", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SecretStoreError("macOS security command is not available") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "security command failed").strip()
        raise SecretStoreError(message) from exc
