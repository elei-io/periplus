import os
from pathlib import Path

from crawl4ai import LLMConfig
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
_DEFAULT_MODEL = "openai/gpt-5.5"


def openrouter_llm_config(*model_env_names: str) -> LLMConfig:
    load_dotenv(_ENV_PATH)
    provider = next((os.getenv(name) for name in model_env_names if os.getenv(name)), None)
    provider = provider or os.getenv("LLM_PROVIDER") or _DEFAULT_MODEL
    if not provider.startswith("openrouter/"):
        provider = f"openrouter/{provider}"

    return LLMConfig(provider=provider, api_token=os.getenv("OPENROUTER_API_KEY"))
