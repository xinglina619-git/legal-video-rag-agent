from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from app.config import Settings

SUPPORTED_PROVIDERS = ("ollama", "openai", "deepseek", "dashscope", "siliconflow", "custom")
DEFAULT_MODELS = {"deepseek": "deepseek-v4-flash"}


def get_chat_model(settings: Settings, provider: str | None = None, model: str | None = None):
    selected = (provider or settings.model_provider).lower()
    if selected not in SUPPORTED_PROVIDERS:
        raise RuntimeError(f"Unsupported provider: {selected}")
    if selected == "ollama":
        name = model or settings.ollama_chat_model
        if not name:
            raise RuntimeError("OLLAMA_CHAT_MODEL is required for local mode")
        return ChatOllama(base_url=settings.ollama_base_url, model=name, temperature=0)
    key = getattr(settings, f"{selected}_api_key")
    base = getattr(settings, f"{selected}_base_url")
    name = model or getattr(settings, f"{selected}_model") or DEFAULT_MODELS.get(selected)
    if not key or not base or not name:
        raise RuntimeError(f"{selected} requires key, base URL and model in .env.local")
    return ChatOpenAI(api_key=key, base_url=base, model=name, temperature=0)


def get_chat_model_with_fallback(
    settings: Settings, provider: str | None = None, model: str | None = None
):
    """Prefer the selected provider and fall back to configured Ollama without hiding errors."""
    try:
        return get_chat_model(settings, provider, model)
    except RuntimeError:
        if (provider or settings.model_provider).lower() != "ollama" and settings.ollama_chat_model:
            return get_chat_model(settings, "ollama", None)
        raise
