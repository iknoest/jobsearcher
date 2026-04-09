"""Multi-LLM router with quota-aware fallback."""

import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone

import yaml
from dotenv import load_dotenv

load_dotenv()

QUOTA_FILE = Path("output/llm_quota_usage.json")


def load_llm_config(config_path="config/llm_config.yaml"):
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_quota_usage():
    """Load daily usage counts from file."""
    if QUOTA_FILE.exists():
        with open(QUOTA_FILE, "r") as f:
            data = json.load(f)
        # Reset if it's a new day
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if data.get("date") != today:
            return {"date": today, "usage": {}}
        return data
    return {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "usage": {}}


def save_quota_usage(data):
    QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUOTA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_available_model(config=None):
    """Return the highest-priority model that hasn't hit its quota."""
    if config is None:
        config = load_llm_config()

    usage = load_quota_usage()
    models = sorted(config["models"].items(), key=lambda x: x[1]["priority"])

    for name, model_config in models:
        api_key = os.getenv(model_config["env_key"], "")
        if not api_key:
            continue
        used = usage.get("usage", {}).get(name, 0)
        if used < model_config["daily_quota"]:
            return name, model_config

    # All quotas exhausted — return lowest-priority as fallback
    for name, model_config in models:
        if os.getenv(model_config["env_key"], ""):
            return name, model_config

    raise RuntimeError("No LLM API keys configured. Set at least one in .env")


def increment_usage(model_name):
    usage = load_quota_usage()
    usage.setdefault("usage", {})[model_name] = usage.get("usage", {}).get(model_name, 0) + 1
    save_quota_usage(usage)


def call_llm(prompt, config=None, max_retries=4):
    """Call the best available LLM. Retries with backoff on rate limits."""
    if config is None:
        config = load_llm_config()

    for attempt in range(max_retries + 1):
        model_name, model_config = get_available_model(config)
        provider = model_config["provider"]

        try:
            if provider == "anthropic":
                result = _call_anthropic(prompt, model_config)
            elif provider == "google":
                result = _call_gemini(prompt, model_config)
            elif provider == "openai":
                result = _call_openai(prompt, model_config)
            elif provider == "openrouter":
                result = _call_openrouter(prompt, model_config)
            else:
                raise ValueError(f"Unknown provider: {provider}")

            increment_usage(model_name)
            print(f"    [LLM] Used: {model_name}")
            return result

        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "rate" in err_str.lower()
            is_auth_error = "401" in err_str or "403" in err_str or "invalid" in err_str.lower()

            if is_auth_error:
                # Bad key — mark exhausted permanently, try next provider
                print(f"    [LLM] {model_name} auth failed, skipping")
                usage = load_quota_usage()
                usage.setdefault("usage", {})[model_name] = model_config["daily_quota"]
                save_quota_usage(usage)
            elif is_rate_limit and attempt < max_retries:
                # Rate limited — wait and retry same provider
                wait = min(20 * (attempt + 1), 60)
                print(f"    [LLM] {model_name} rate limited, waiting {wait}s (attempt {attempt+1}/{max_retries})...")
                time.sleep(wait)
            else:
                print(f"    [LLM] {model_name} failed: {err_str[:100]}")
                usage = load_quota_usage()
                usage.setdefault("usage", {})[model_name] = model_config["daily_quota"]
                save_quota_usage(usage)

    raise RuntimeError("All LLM providers failed after retries")


def _call_anthropic(prompt, config):
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=config["model"],
        max_tokens=config.get("max_output_tokens", 1500),
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _call_gemini(prompt, config):
    from google import genai

    client = genai.Client(api_key=os.getenv(config["env_key"]))
    response = client.models.generate_content(
        model=config["model"],
        contents=prompt,
    )
    return response.text


def _call_openai(prompt, config):
    from openai import OpenAI

    client = OpenAI()
    response = client.chat.completions.create(
        model=config["model"],
        max_tokens=config.get("max_output_tokens", 1500),
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


OPENROUTER_FREE_MODELS = [
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "minimax/minimax-m2.5:free",
    "google/gemma-3-27b-it:free",
    "stepfun/step-3.5-flash:free",
    "openai/gpt-oss-120b:free",
]


def _call_openrouter(prompt, config):
    from openai import OpenAI

    api_key = os.getenv(config["env_key"])
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    # Try configured model first, then fallback through free models
    models_to_try = [config["model"]] + [m for m in OPENROUTER_FREE_MODELS if m != config["model"]]

    for i, model_id in enumerate(models_to_try):
        try:
            response = client.chat.completions.create(
                model=model_id,
                max_tokens=config.get("max_output_tokens", 1500),
                messages=[{"role": "user", "content": prompt}],
            )
            if response.choices and response.choices[0].message.content:
                return response.choices[0].message.content
        except Exception as e:
            if "429" in str(e):
                time.sleep(2)  # Brief pause before trying next model
                continue
            raise  # Re-raise non-rate-limit errors

    raise RuntimeError("All OpenRouter free models rate-limited")
