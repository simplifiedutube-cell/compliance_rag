"""Generation providers.

  groq       FREE and the right default for this demo. 30 req/min, 1,000
             req/day on llama-3.3-70b-versatile. Critically, Groq's Services
             Agreement s4.2 contractually bars training on your inputs or
             outputs -- the only free tier here that does. You can quote that
             clause to a compliance prospect.
  anthropic  Best quality. No free tier; ~$5 minimum credit.
  gemini     Free of charge, but Google's terms say free-tier input/output is
             used to improve their products and human reviewers may read it.
             Synthetic demo data only -- never a client's documents.
  ollama     Fully local. No key, no network. A 4-9B model with constrained
             JSON output is adequate for grounded extractive answers; slower,
             and weaker on multi-hop. Good offline fallback.

Every provider is asked for strict JSON. Ollama and Gemini support schema-
constrained decoding, which removes malformed JSON as a failure mode.
"""
from __future__ import annotations

import json
import re

import config


class GenerationError(RuntimeError):
    pass


def available() -> bool:
    p = config.LLM_PROVIDER
    return bool(
        (p == "groq" and config.GROQ_API_KEY)
        or (p == "anthropic" and config.ANTHROPIC_API_KEY)
        or (p == "gemini" and config.GEMINI_API_KEY)
        or (p == "ollama")
    )


def provider_label() -> str:
    p = config.LLM_PROVIDER
    model = {
        "groq": config.GROQ_MODEL,
        "anthropic": config.ANTHROPIC_MODEL,
        "gemini": config.GEMINI_MODEL,
        "ollama": config.OLLAMA_MODEL,
    }.get(p, "?")
    return f"{p} · {model}"


def trains_on_your_data() -> str:
    """Shown in the dashboard. Prospects in this market will ask."""
    return {
        "groq": "No — Groq Services Agreement §4.2 bars training on inputs/outputs",
        "anthropic": "No — API data not used for training by default",
        "gemini": "YES on the free tier — Google's terms allow product "
                  "improvement and human review. Synthetic data only.",
        "ollama": "No — runs entirely on this machine, no network egress",
    }.get(config.LLM_PROVIDER, "unknown")


# ------------------------------------------------------------------ backends

def _groq(system: str, user: str, max_tokens: int) -> str:
    from groq import Groq
    client = Groq(api_key=config.GROQ_API_KEY)
    r = client.chat.completions.create(
        model=config.GROQ_MODEL,
        max_tokens=max_tokens,
        temperature=0,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return r.choices[0].message.content or ""


def _anthropic(system: str, user: str, max_tokens: int) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    r = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return r.content[0].text


def _gemini(system: str, user: str, max_tokens: int) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    r = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            temperature=0,
        ),
    )
    return r.text or ""


def _ollama(system: str, user: str, max_tokens: int) -> str:
    import httpx
    r = httpx.post(
        f"{config.OLLAMA_HOST}/api/chat",
        timeout=180,
        json={
            "model": config.OLLAMA_MODEL,
            "stream": False,
            "options": {"temperature": 0, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        },
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


_BACKENDS = {"groq": _groq, "anthropic": _anthropic,
             "gemini": _gemini, "ollama": _ollama}


def generate_text(system: str, user: str, *, max_tokens: int = 120) -> str:
    fn = _BACKENDS.get(config.LLM_PROVIDER)
    if fn is None:
        raise GenerationError(f"Unknown LLM_PROVIDER: {config.LLM_PROVIDER}")
    return (fn(system, user, max_tokens) or "").strip()


def generate_json(system: str, user: str, *, max_tokens: int = 1600) -> dict:
    fn = _BACKENDS.get(config.LLM_PROVIDER)
    if fn is None:
        raise GenerationError(f"Unknown LLM_PROVIDER: {config.LLM_PROVIDER}")

    text = (fn(system, user, max_tokens) or "").strip()
    if not text:
        raise GenerationError("empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise GenerationError(f"no JSON in response: {text[:180]}") from None
        return json.loads(m.group(0))
