from crawl4ai import LLMConfig
from config import get_optional, get_str


def openrouter_llm_config(*model_env_names: str) -> LLMConfig:
    provider = next((get_optional(name) for name in model_env_names if get_optional(name)), None)
    provider = provider or get_str("LLM_PROVIDER")
    if not provider.startswith("openrouter/"):
        provider = f"openrouter/{provider}"

    return LLMConfig(provider=provider, api_token=get_optional("OPENROUTER_API_KEY"))
