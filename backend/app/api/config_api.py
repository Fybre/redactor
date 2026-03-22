import logging
import uuid
from typing import List, Optional
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import load_runtime_config, save_runtime_config, settings
from app.core.presidio_engine import get_supported_entities
from app.core.redaction_levels import ENTITY_DESCRIPTIONS, LEVEL_DESCRIPTIONS
from app.models.schemas import SystemConfig, ProfileCreate, WebhookConfig

router = APIRouter()
logger = logging.getLogger(__name__)

_OLLAMA_LIST_TIMEOUT  = 5.0    # seconds — quick availability check
_OLLAMA_PULL_TIMEOUT  = 600.0  # seconds — model pulls can be large


@router.get("", response_model=SystemConfig)
async def get_config():
    return load_runtime_config()


@router.put("")
async def update_config(config: SystemConfig):
    current = load_runtime_config()
    current.update(config.model_dump())
    save_runtime_config(current)
    return {"status": "saved"}


@router.get("/entities")
async def list_entities():
    """Return all supported entity types with descriptions and recognizer metadata."""
    from app.core.presidio_engine import get_entity_info
    try:
        info = get_entity_info()
        supported = list(info.keys())
    except Exception:
        info = {}
        supported = list(ENTITY_DESCRIPTIONS.keys())
    all_types = list(supported) + [e for e in ENTITY_DESCRIPTIONS if e not in supported]
    return [
        {
            "type": e,
            "description": ENTITY_DESCRIPTIONS.get(e, e.replace("_", " ").title()),
            "recognizer_type": info.get(e, {}).get("recognizer_type", "pattern"),
            "recognizer_name": info.get(e, {}).get("recognizer_name", ""),
            "custom": info.get(e, {}).get("custom", False),
        }
        for e in all_types
    ]


# --- Custom Recognizers ---

class PatternConfig(BaseModel):
    name: str = "pattern"
    regex: str
    score: float = 0.5


class RecognizerCreate(BaseModel):
    name: str
    entity_type: str
    type: str                            # "pattern" or "deny_list"
    description: Optional[str] = ""     # used in LLM prompt when strategy includes LLM
    patterns: Optional[List[PatternConfig]] = None
    deny_list: Optional[List[str]] = None
    context: Optional[List[str]] = None


@router.get("/recognizers")
async def list_recognizers():
    config = load_runtime_config()
    return config.get("custom_recognizers", [])


@router.post("/recognizers")
async def add_recognizer(rec: RecognizerCreate):
    from app.core.presidio_engine import load_custom_recognizers
    config = load_runtime_config()
    recognizers = config.get("custom_recognizers", [])
    entry = {
        "id": str(uuid.uuid4()),
        "name": rec.name,
        "entity_type": rec.entity_type,
        "type": rec.type,
        "description": rec.description or "",
        "patterns": [p.model_dump() for p in (rec.patterns or [])],
        "deny_list": rec.deny_list or [],
        "context": rec.context or [],
    }
    recognizers.append(entry)
    config["custom_recognizers"] = recognizers
    save_runtime_config(config)
    load_custom_recognizers(recognizers)
    return {"status": "added", "id": entry["id"]}


@router.delete("/recognizers/{rec_id}")
async def delete_recognizer(rec_id: str):
    from app.core.presidio_engine import load_custom_recognizers
    config = load_runtime_config()
    recognizers = config.get("custom_recognizers", [])
    config["custom_recognizers"] = [r for r in recognizers if r.get("id") != rec_id]
    save_runtime_config(config)
    load_custom_recognizers(config["custom_recognizers"])
    return {"status": "deleted"}


@router.get("/levels")
async def list_levels():
    from app.core.redaction_levels import ENTITY_LEVELS
    return [
        {
            "level": level,
            "description": LEVEL_DESCRIPTIONS.get(level, ""),
            "entity_count": len(entities),
        }
        for level, entities in {**ENTITY_LEVELS, "custom": []}.items()
    ]


# --- Profiles ---

@router.get("/profiles")
async def list_profiles():
    config = load_runtime_config()
    return config.get("profiles", {})


@router.post("/profiles")
async def create_profile(profile: ProfileCreate):
    from app.config import _DEFAULT_RUNTIME_CONFIG
    config = load_runtime_config()
    profiles = config.get("profiles", {})
    existing = profiles.get(profile.name)
    # Allow overriding built-ins via POST; only block duplicates of user-created profiles
    if existing and not existing.get("builtin"):
        raise HTTPException(status_code=409, detail=f"Profile '{profile.name}' already exists")
    entry = {"entities": profile.entities, "description": profile.description or ""}
    if profile.strategy:
        entry["strategy"] = profile.strategy
    profiles[profile.name] = entry
    config["profiles"] = profiles
    save_runtime_config(config)
    return {"status": "created", "name": profile.name}


@router.put("/profiles/{name}")
async def update_profile(name: str, profile: ProfileCreate):
    config = load_runtime_config()
    profiles = config.get("profiles", {})
    if name not in profiles:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    entry = {"entities": profile.entities, "description": profile.description or ""}
    if profile.strategy:
        entry["strategy"] = profile.strategy
    profiles[name] = entry
    config["profiles"] = profiles
    save_runtime_config(config)
    return {"status": "updated", "name": name}


@router.post("/profiles/{name}/duplicate")
async def duplicate_profile(name: str):
    config = load_runtime_config()
    profiles = config.get("profiles", {})
    if name not in profiles:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    base = f"{name}_copy"
    new_name = base
    n = 2
    while new_name in profiles:
        new_name = f"{base}_{n}"
        n += 1
    source = profiles[name]
    dup = {
        "entities": list(source.get("entities", [])),
        "description": source.get("description", ""),
    }
    if source.get("strategy"):
        dup["strategy"] = source["strategy"]
    profiles[new_name] = dup
    config["profiles"] = profiles
    save_runtime_config(config)
    return {"status": "duplicated", "name": new_name}


@router.delete("/profiles/{name}")
async def delete_profile(name: str):
    import json as _json
    from app.config import _RUNTIME_CONFIG_PATH
    config = load_runtime_config()
    if name not in config.get("profiles", {}):
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    # Write a None marker so built-in profiles stay deleted across restarts
    saved = {}
    if _RUNTIME_CONFIG_PATH.exists():
        try:
            saved = _json.loads(_RUNTIME_CONFIG_PATH.read_text())
        except Exception:
            pass
    saved.setdefault("profiles", {})[name] = None
    if saved.get("default_profile") == name:
        saved["default_profile"] = None
    save_runtime_config(saved)
    return {"status": "deleted"}


@router.post("/profiles/_restore_defaults")
async def restore_default_profiles():
    """Remove all saved profile overrides (including deletion markers) so built-in defaults reappear."""
    import json as _json
    from app.config import _RUNTIME_CONFIG_PATH
    saved = {}
    if _RUNTIME_CONFIG_PATH.exists():
        try:
            saved = _json.loads(_RUNTIME_CONFIG_PATH.read_text())
        except Exception:
            pass
    saved.pop("profiles", None)
    save_runtime_config(saved)
    return {"status": "restored"}


# --- Watched Folders ---

class WatchedFolderConfig(BaseModel):
    name: str
    path: str
    profile: Optional[str] = None
    output_path: Optional[str] = ""
    enabled: bool = True


@router.get("/watched-folders")
async def list_watched_folders():
    config = load_runtime_config()
    return config.get("watched_folders", [])


@router.post("/watched-folders")
async def add_watched_folder(folder: WatchedFolderConfig):
    config = load_runtime_config()
    folders = config.get("watched_folders", [])
    folders.append({
        "id": str(uuid.uuid4()),
        "name": folder.name,
        "path": folder.path,
        "profile": folder.profile or None,
        "output_path": folder.output_path or "",
        "enabled": folder.enabled,
    })
    config["watched_folders"] = folders
    save_runtime_config(config)
    return {"status": "added"}


@router.put("/watched-folders/{folder_id}")
async def update_watched_folder(folder_id: str, folder: WatchedFolderConfig):
    config = load_runtime_config()
    folders = config.get("watched_folders", [])
    idx = next((i for i, f in enumerate(folders) if f.get("id") == folder_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Watched folder not found")
    folders[idx] = {
        "id": folder_id,
        "name": folder.name,
        "path": folder.path,
        "profile": folder.profile or None,
        "output_path": folder.output_path or "",
        "enabled": folder.enabled,
    }
    config["watched_folders"] = folders
    save_runtime_config(config)
    return {"status": "updated"}


@router.delete("/watched-folders/{folder_id}")
async def delete_watched_folder(folder_id: str):
    config = load_runtime_config()
    folders = config.get("watched_folders", [])
    config["watched_folders"] = [f for f in folders if f.get("id") != folder_id]
    save_runtime_config(config)
    return {"status": "deleted"}


# --- Webhooks ---

@router.get("/webhooks")
async def list_webhooks():
    config = load_runtime_config()
    return config.get("webhooks", [])


@router.post("/webhooks")
async def add_webhook(webhook: WebhookConfig):
    config = load_runtime_config()
    webhooks = config.get("webhooks", [])
    webhooks.append({
        "id": str(uuid.uuid4()),
        "url": webhook.url,
        "secret": webhook.secret,
        "name": webhook.name,
        "enabled": webhook.enabled,
    })
    config["webhooks"] = webhooks
    save_runtime_config(config)
    return {"status": "added"}


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str):
    config = load_runtime_config()
    webhooks = config.get("webhooks", [])
    config["webhooks"] = [w for w in webhooks if w.get("id") != webhook_id]
    save_runtime_config(config)
    return {"status": "deleted"}


# --- Templates ---

_SENSITIVE_HEADER_PATTERNS = ['authorization', 'token', 'key', 'secret', 'password', 'credential', 'auth']


def _is_sensitive_header(name: str) -> bool:
    lower = name.lower()
    return any(p in lower for p in _SENSITIVE_HEADER_PATTERNS)


def _mask_headers(headers: dict) -> dict:
    if settings.allow_header_reveal:
        return headers
    return {k: ("__redacted__" if _is_sensitive_header(k) else v) for k, v in headers.items()}


def _merge_headers(existing: dict, incoming: dict) -> dict:
    if settings.allow_header_reveal:
        return incoming or {}
    merged = dict(incoming or {})
    for k, v in merged.items():
        if _is_sensitive_header(k) and v == "":
            merged[k] = (existing or {}).get(k, "")
    return merged

class TemplateCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    body: str
    headers: Optional[dict] = None        # HTTP headers sent with the rendered webhook POST
    pre_fetch_url: Optional[str] = None   # URL to call before rendering (result available as {{ fetched }})
    pre_fetch_method: Optional[str] = "GET"  # HTTP method for pre-fetch (GET or POST)
    pre_fetch_body: Optional[str] = None  # Jinja2 body template for pre-fetch POST requests


@router.get("/templates")
async def list_templates():
    config = load_runtime_config()
    templates = config.get("webhook_templates", {})
    return [
        {
            "name": name,
            "description": t.get("description", ""),
            "body": t.get("body", ""),
            "headers": _mask_headers(t.get("headers") or {}),
            "pre_fetch_url": t.get("pre_fetch_url") or "",
            "pre_fetch_method": t.get("pre_fetch_method") or "GET",
            "pre_fetch_body": t.get("pre_fetch_body") or "",
        }
        for name, t in templates.items()
    ]


@router.post("/templates")
async def create_template(template: TemplateCreate):
    config = load_runtime_config()
    templates = config.setdefault("webhook_templates", {})
    if template.name in templates:
        raise HTTPException(status_code=409, detail=f"Template '{template.name}' already exists")
    templates[template.name] = {
        "description": template.description,
        "body": template.body,
        "headers": template.headers or {},
        "pre_fetch_url": template.pre_fetch_url or "",
        "pre_fetch_method": template.pre_fetch_method or "GET",
        "pre_fetch_body": template.pre_fetch_body or "",
    }
    save_runtime_config(config)
    return {"status": "created", "name": template.name}


@router.put("/templates/{name}")
async def update_template(name: str, template: TemplateCreate):
    config = load_runtime_config()
    templates = config.setdefault("webhook_templates", {})
    if name not in templates:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")
    new_name = template.name.strip()
    if new_name != name and new_name in templates:
        raise HTTPException(status_code=409, detail=f"Template '{new_name}' already exists")
    updated = {
        "description": template.description,
        "body": template.body,
        "headers": _merge_headers(templates[name].get("headers", {}), template.headers),
        "pre_fetch_url": template.pre_fetch_url or "",
        "pre_fetch_method": template.pre_fetch_method or "GET",
        "pre_fetch_body": template.pre_fetch_body or "",
    }
    if new_name != name:
        # Rebuild dict to preserve insertion order with the new key in place of the old
        templates = {(new_name if k == name else k): (updated if k == name else v)
                     for k, v in templates.items()}
        config["webhook_templates"] = templates
    else:
        templates[name] = updated
    save_runtime_config(config)
    return {"status": "updated", "name": new_name}


@router.post("/templates/{name}/duplicate")
async def duplicate_template(name: str):
    config = load_runtime_config()
    templates = config.setdefault("webhook_templates", {})
    if name not in templates:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")
    base = f"{name}_copy"
    new_name = base
    n = 2
    while new_name in templates:
        new_name = f"{base}_{n}"
        n += 1
    templates[new_name] = dict(templates[name])
    save_runtime_config(config)
    return {"status": "duplicated", "name": new_name}


@router.delete("/templates/{name}")
async def delete_template(name: str):
    import json as _json
    from app.config import _RUNTIME_CONFIG_PATH
    # Check the merged view to validate the name exists
    config = load_runtime_config()
    if name not in config.get("webhook_templates", {}):
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")
    # Write a None marker into the raw saved config so the merge logic
    # knows this template was explicitly deleted (even if it's a built-in default).
    saved = {}
    if _RUNTIME_CONFIG_PATH.exists():
        try:
            saved = _json.loads(_RUNTIME_CONFIG_PATH.read_text())
        except Exception:
            pass
    saved.setdefault("webhook_templates", {})[name] = None
    save_runtime_config(saved)
    return {"status": "deleted"}


@router.post("/templates/_restore_defaults")
async def restore_default_templates():
    """Remove all saved webhook_templates overrides (including deletion markers) so
    the built-in defaults show through again on next load."""
    import json as _json
    from app.config import _RUNTIME_CONFIG_PATH
    saved = {}
    if _RUNTIME_CONFIG_PATH.exists():
        try:
            saved = _json.loads(_RUNTIME_CONFIG_PATH.read_text())
        except Exception:
            pass
    saved.pop("webhook_templates", None)
    save_runtime_config(saved)
    return {"status": "restored"}


def _ollama_base(config: dict) -> str:
    """Derive the Ollama base URL (without /v1) from the configured llm_base_url."""
    url = config.get("llm_base_url", "http://ollama:11434/v1").rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


class OllamaPullRequest(BaseModel):
    model: str


@router.get("/ollama/models")
async def list_ollama_models():
    """List models currently available in the configured Ollama instance."""
    config = load_runtime_config()
    base = _ollama_base(config)
    try:
        async with httpx.AsyncClient(timeout=_OLLAMA_LIST_TIMEOUT) as client:
            resp = await client.get(f"{base}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                return {"models": [m["name"] for m in data.get("models", [])]}
            return {"models": []}
    except Exception as e:
        logger.debug(f"Ollama model list failed: {e}")
        return {"models": [], "unavailable": True}


@router.post("/ollama/pull")
async def pull_ollama_model(body: OllamaPullRequest):
    """Pull a model from Ollama, streaming progress as Server-Sent Events."""
    config = load_runtime_config()
    base = _ollama_base(config)

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=_OLLAMA_PULL_TIMEOUT) as client:
                async with client.stream(
                    "POST", f"{base}/api/pull", json={"name": body.model}
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            yield f"data: {line}\n\n"
        except Exception as e:
            import json as _json
            yield f"data: {_json.dumps({'error': str(e)})}\n\n"
        yield 'data: {"done":true}\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(webhook_id: str):
    from app.utils.webhook_sender import send_webhook
    config = load_runtime_config()
    webhooks = config.get("webhooks", [])
    wh = next((w for w in webhooks if w.get("id") == webhook_id), None)
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")
    success = await send_webhook(
        wh["url"],
        {"event": "test", "message": "Webhook test from Redactor"},
        secret=wh.get("secret"),
    )
    return {"success": success}
