from __future__ import annotations

import os
import platform
import subprocess


KEYCHAIN_PREFIX = "omni-hub"
_MEMORY_SECRETS: dict[str, str] = {}


class SecretStoreError(RuntimeError):
    pass


def store_api_key(account_id: str, api_key: str) -> str:
    key = api_key.strip()
    if not key:
        raise SecretStoreError("api key is empty")
    target = _target(account_id)
    if _backend() == "memory":
        _MEMORY_SECRETS[target] = key
        return f"keychain:{target}"
    if platform.system() != "Darwin":
        raise SecretStoreError("macOS Keychain is required for local secret storage")
    _run_security(
        [
            "add-generic-password",
            "-a",
            account_id,
            "-s",
            target,
            "-w",
            key,
            "-U",
        ]
    )
    return f"keychain:{target}"


def resolve_secret_ref(secret_ref: str) -> str:
    ref = secret_ref.strip()
    if not ref:
        return ""
    prefix, _, value = ref.partition(":")
    if prefix == "env":
        return os.environ.get(value, "")
    if prefix == "runtime":
        return _MEMORY_SECRETS.get(value, "")
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
    return os.environ.get("OMNI_HUB_SECRET_BACKEND", "keychain").strip().lower()


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
