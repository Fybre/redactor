from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import load_runtime_config, save_runtime_config
from app.core.presidio_engine import get_supported_entities
from app.core.redaction_levels import ENTITY_DESCRIPTIONS, LEVEL_DESCRIPTIONS
from app.models.schemas import SystemConfig, ProfileCreate, WebhookConfig

router = APIRouter()


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
    return [
        {
            "type": e,
            "description": ENTITY_DESCRIPTIONS.get(e, e.replace("_", " ").title()),
        }
        for e in supported
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
    import uuid
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
            "headers": t.get("headers") or {},
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
    templates[name] = {
        "description": template.description,
        "body": template.body,
        "headers": template.headers or {},
    }
    save_runtime_config(config)
    return {"status": "updated", "name": name}


@router.delete("/templates/{name}")
async def delete_template(name: str):
    config = load_runtime_config()
    templates = config.get("webhook_templates", {})
    if name not in templates:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")
    del templates[name]
    save_runtime_config(config)
    return {"status": "deleted"}


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
