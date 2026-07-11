from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///storage/legal_agent.db"
    qdrant_url: str = "local"
    evidence_collection: str = "legal_evidence"
    authority_collection: str = "legal_authorities"
    storage_dir: Path = Path("storage")
    model_provider: str = "deepseek"
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str | None = "deepseek-v4-flash"
    dashscope_api_key: str | None = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str | None = None
    siliconflow_api_key: str | None = None
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_model: str | None = None
    custom_api_key: str | None = None
    custom_base_url: str | None = None
    custom_model: str | None = None
    dense_embedding_model: str = "BAAI/bge-small-zh-v1.5"
    sparse_embedding_model: str = "Qdrant/bm25"
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_download_root: Path = Path("storage/models")
    media_retry_attempts: int = 3
    media_retry_delay_seconds: float = 2.0
    video_ocr_enabled: bool = True
    video_ocr_interval_seconds: int = 15
    ocr_min_confidence: float = 0.55
    official_web_search_enabled: bool = False
    official_web_search_timeout_seconds: int = 12
    official_web_search_limit: int = 3
    hf_token: str | None = None
    pageindex_home: Path = Path(".vendor/pageindex")
    model_config = SettingsConfigDict(
        env_file=".env.local", env_file_encoding="utf-8", extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
