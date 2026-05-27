from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SERVICE_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "id": "metapi",
        "name": "Metapi",
        "path": "api-management/metapi",
        "endpoint": "http://127.0.0.1:4000",
        "health_url": "http://127.0.0.1:4000/",
        "admin_url": "http://127.0.0.1:4000",
        "fork_url": "https://github.com/Khadgar123/metapi",
        "upstream_url": "https://github.com/cita-777/metapi",
        "role": "upstream account, balance, model discovery, token, and cost routing management",
    },
    {
        "id": "ccload",
        "name": "ccLoad",
        "path": "api-management/ccLoad",
        "endpoint": "http://127.0.0.1:8080",
        "health_url": "http://127.0.0.1:8080/health",
        "admin_url": "http://127.0.0.1:8080/web/",
        "fork_url": "https://github.com/Khadgar123/ccLoad",
        "upstream_url": "https://github.com/caidaoli/ccLoad",
        "role": "local Claude Code, Codex, Gemini, and OpenAI-compatible gateway",
    },
)


def api_management_dir(workspace: Path | str = ".") -> Path:
    return Path(workspace).resolve() / "api-management"


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _probe_http(url: str, timeout_seconds: float) -> dict[str, object]:
    request = Request(
        url,
        method="GET",
        headers={"User-Agent": "omni-hub-api-management-status/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return {
                "url": url,
                "reachable": True,
                "status_code": response.status,
                "error": "",
            }
    except HTTPError as exc:
        return {
            "url": url,
            "reachable": True,
            "status_code": exc.code,
            "error": "",
        }
    except (OSError, URLError) as exc:
        return {
            "url": url,
            "reachable": False,
            "status_code": None,
            "error": str(exc),
        }


def load_api_management_defaults(workspace: Path | str = ".") -> dict[str, object]:
    defaults_file = api_management_dir(workspace) / "defaults.json"
    if not defaults_file.exists():
        return {
            "file_exists": False,
            "path": "api-management/defaults.json",
            "default_project": "",
            "default_provider": "",
            "default_model": "",
            "providers": {},
        }

    try:
        data = json.loads(defaults_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "file_exists": True,
            "path": "api-management/defaults.json",
            "error": f"invalid JSON: {exc}",
            "providers": {},
        }

    if not isinstance(data, dict):
        return {
            "file_exists": True,
            "path": "api-management/defaults.json",
            "error": "defaults root must be an object",
            "providers": {},
        }

    return {
        "file_exists": True,
        "path": "api-management/defaults.json",
        "version": data.get("version", 1),
        "default_project": str(data.get("default_project", "")),
        "default_provider": str(data.get("default_provider", "")),
        "default_model": str(data.get("default_model", "")),
        "providers": data.get("providers", {}),
    }


def _default_secret_status(defaults: dict[str, object]) -> dict[str, object]:
    providers = defaults.get("providers")
    if not isinstance(providers, dict):
        return {"secret_ref": "", "present": False}

    default_provider = str(defaults.get("default_provider", ""))
    provider = providers.get(default_provider)
    if not isinstance(provider, dict):
        return {"secret_ref": "", "present": False}

    secret_ref = str(provider.get("secret_ref", "")).strip()
    if not secret_ref:
        return {"secret_ref": "", "present": False}

    try:
        from .secrets import has_secret

        present = has_secret(secret_ref)
    except Exception:
        present = False

    return {
        "secret_ref": secret_ref,
        "present": present,
    }


def api_management_status(
    workspace: Path | str = ".",
    timeout_seconds: float = 0.5,
) -> dict[str, object]:
    workspace_root = Path(workspace).resolve()
    management_dir = api_management_dir(workspace_root)
    compose_file = management_dir / "compose.yml"
    compose_build_file = management_dir / "compose.build.yml"
    env_example = management_dir / "env.example"

    services: list[dict[str, object]] = []
    for definition in SERVICE_DEFINITIONS:
        service_path = workspace_root / definition["path"]
        probe = _probe_http(definition["health_url"], timeout_seconds)
        services.append(
            {
                "id": definition["id"],
                "name": definition["name"],
                "role": definition["role"],
                "path": _relative(workspace_root, service_path),
                "path_exists": service_path.exists(),
                "is_git_checkout": (service_path / ".git").exists(),
                "endpoint": definition["endpoint"],
                "admin_url": definition["admin_url"],
                "fork_url": definition["fork_url"],
                "upstream_url": definition["upstream_url"],
                "health": probe,
            }
        )

    ready_for_local_run = (
        management_dir.exists()
        and compose_file.exists()
        and compose_build_file.exists()
        and env_example.exists()
        and all(bool(service["path_exists"]) for service in services)
    )
    all_reachable = all(
        bool(service["health"]["reachable"]) for service in services  # type: ignore[index]
    )
    defaults = load_api_management_defaults(workspace_root)

    return {
        "status": "reachable" if all_reachable else "configured",
        "ready_for_local_run": ready_for_local_run,
        "all_services_reachable": all_reachable,
        "api_management_dir": _relative(workspace_root, management_dir),
        "defaults": {
            "file_exists": defaults.get("file_exists", False),
            "path": defaults.get("path", "api-management/defaults.json"),
            "default_project": defaults.get("default_project", ""),
            "default_provider": defaults.get("default_provider", ""),
            "default_model": defaults.get("default_model", ""),
            "secret": _default_secret_status(defaults),
        },
        "compose": {
            "image_command": "docker compose --env-file api-management/env.example -f api-management/compose.yml up -d",
            "build_command": "docker compose --env-file api-management/env.example -f api-management/compose.yml -f api-management/compose.build.yml up -d --build",
            "config_check": "docker compose --env-file api-management/env.example -f api-management/compose.yml config",
            "compose_file_exists": compose_file.exists(),
            "compose_build_file_exists": compose_build_file.exists(),
            "env_example_exists": env_example.exists(),
        },
        "services": services,
    }
