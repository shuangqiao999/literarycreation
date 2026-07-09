"""LiteraryCreation config API — delegates to providers.registry."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from literarycreation.core.providers import _mask_key, registry

router = APIRouter(prefix="/api/forge/config", tags=["config"])


class LLMConfigUpdate(BaseModel):
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    provider_slug: str = ""
    llm_temperature: float = 0.85


class EmbedConfigUpdate(BaseModel):
    embedding_api_base: str = ""
    embedding_api_key: str = ""
    embedding_model_name: str = ""
    provider_slug: str = ""


class ModelListRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""


# ── Provider catalog ──
@router.get("/providers")
async def list_providers():
    return {"providers": registry.get_providers()}


# ── LLM config ──
@router.get("/llm")
async def get_llm_config():
    return {
        "provider_slug": registry.llm_provider_slug,
        "llm_base_url": registry.llm_base_url,
        "llm_model": registry.llm_model,
        "llm_api_key": _mask_key(registry.llm_api_key),
        "llm_temperature": registry.llm_temperature,
    }

@router.post("/llm")
async def update_llm(body: LLMConfigUpdate):
    if body.llm_base_url or body.llm_base_url == "":
        registry.llm_base_url = body.llm_base_url.rstrip("/")
    if body.llm_api_key != "••••••••": registry.llm_api_key = body.llm_api_key
    if body.llm_model or body.llm_model == "":
        registry.llm_model = body.llm_model
    if body.provider_slug or body.provider_slug == "":
        registry.llm_provider_slug = body.provider_slug
    registry.llm_temperature = body.llm_temperature
    registry.save()
    return {"status": "ok"}


# ── Embedding config ──
@router.get("/embedding")
async def get_embed_config():
    return {
        "provider_slug": registry.embed_provider_slug,
        "embedding_api_base": registry.embedding_api_base,
        "embedding_model_name": registry.embedding_model_name,
        "embedding_api_key": _mask_key(registry.embedding_api_key),
    }

@router.post("/embedding")
async def update_embed(body: EmbedConfigUpdate):
    if body.embedding_api_base or body.embedding_api_base == "":
        registry.embedding_api_base = body.embedding_api_base.rstrip("/")
    if body.embedding_api_key != "••••••••": registry.embedding_api_key = body.embedding_api_key
    if body.embedding_model_name or body.embedding_model_name == "":
        registry.embedding_model_name = body.embedding_model_name
    if body.provider_slug or body.provider_slug == "":
        registry.embed_provider_slug = body.provider_slug
    registry.save()
    return {"status": "ok"}


# ── Engine config ──

class EngineConfigUpdate(BaseModel):
    max_agents: int = 10000
    max_concurrent: int = 2
    retrieve_top_k: int = 5


@router.get("/engine")
async def get_engine_config():
    from literarycreation.core.config import config as _cfg
    d = registry._data
    return {
        "max_agents": int(d.get("max_agents") or _cfg.deduction_max_agents),
        "max_concurrent": int(d.get("max_concurrent") or _cfg.deduction_max_concurrent),
        "retrieve_top_k": int(d.get("retrieve_top_k") or _cfg.deduction_retrieve_top_k),
    }


@router.post("/engine")
async def update_engine(body: EngineConfigUpdate):
    d = registry._data
    d["max_agents"] = body.max_agents
    d["max_concurrent"] = body.max_concurrent
    d["retrieve_top_k"] = body.retrieve_top_k
    registry.save()
    return {"status": "ok"}


# ── Model listing + test ──
def _real_key_or(req_key: str) -> str:
    """脱敏串(含 * / •)、空、或 'local' 时直接返回空字符串。
    本地服务无需 key，云端服务需用户显式输入。不再回退到存储值。"""
    k = (req_key or "").strip()
    if not k or k.lower() == "local" or "*" in k or "•" in k:
        return ""
    return k


@router.post("/list-models")
async def list_models(body: ModelListRequest):
    return await registry.list_models(body.base_url, _real_key_or(body.api_key))


@router.post("/test-connection")
async def test_connection(body: ModelListRequest):
    return await registry.test_connection(body.base_url, _real_key_or(body.api_key))


# ── Reload ──
@router.post("/reload")
async def reload():
    registry.reload()
    return {"status": "ok"}
