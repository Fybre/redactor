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
    """Return all supported entity types with descriptions."""
    try:
        supported = get_supported_entities()
    except Exception:
        supported = list(ENTITY_DESCRIPTIONS.keys())
    # Include any extra entities defined in ENTITY_DESCRIPTIONS (e.g. LLM-only types)
    all_types = list(supported) + [e for e in ENTITY_DESCRIPTIONS if e not in supported]
    return [
        {
            "type": e,
            "description": ENTITY_DESCRIPTIONS.get(e, e.replace("_", " ").title()),
        }
        for e in all_types
    ]


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
    config = load_runtime_config()
    profiles = config.get("profiles", {})
    if profile.name in profiles:
        raise HTTPException(status_code=409, detail=f"Profile '{profile.name}' already exists")
    profiles[profile.name] = {
        "entities": profile.entities,
        "description": profile.description or "",
    }
    config["profiles"] = profiles
    save_runtime_config(config)
    return {"status": "created", "name": profile.name}


@router.put("/profiles/{name}")
async def update_profile(name: str, profile: ProfileCreate):
    config = load_runtime_config()
    profiles = config.get("profiles", {})
    if name not in profiles:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    profiles[name] = {
        "entities": profile.entities,
        "description": profile.description or "",
    }
    config["profiles"] = profiles
    save_runtime_config(config)
    return {"status": "updated", "name": name}


@router.delete("/profiles/{name}")
async def delete_profile(name: str):
    config = load_runtime_config()
    profiles = config.get("profiles", {})
    if name not in profiles:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    del profiles[name]
    if config.get("default_profile") == name:
        config["default_profile"] = None
    config["profiles"] = profiles
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
    headers: Optional[dict] = None   # HTTP headers sent with the rendered webhook POST


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
    config = load_runtime_config()
    templates = config.get("webhook_templates", {})
    if name not in templates:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")
    del templates[name]
    save_runtime_config(config)
    return {"status": "deleted"}


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
