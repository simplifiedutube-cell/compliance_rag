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
  nvidia     NVIDIA NIM (build.nvidia.com). Free credits, OpenAI-compatible
             endpoint, and far looser rate limits than the Groq/Gemini free
             tiers -- the practical choice for running the full eval. Their
             data-use position is NOT confirmed here: synthetic data only.
  ollama     Fully local. No key, no network. A 4-9B model with constrained
             JSON output is adequate for grounded extractive answers; slower,
             and weaker on multi-hop. Good offline fallback.

Every provider is asked for strict JSON. Ollama and Gemini support schema-
constrained decoding, which removes malformed JSON as a failure mode.
"""
from __future__ import annotations

import json
import re
import time

import config


class GenerationError(RuntimeError):
    pass


# Every free tier here is rate-limited tightly enough that a 25-case eval run
# will trip it. Without this, answer.py catches the 429 and reports it as a
# low_confidence refusal -- an infrastructure failure that reads as a quality
# problem. See the except branch in answer.ask().
_RETRY_ATTEMPTS = 4
_RATE_LIMITED = ("429", "resource_exhausted", "rate limit", "rate_limit",
                 "too many requests", "quota")


def _retry_after(msg: str, attempt: int) -> float:
    """Prefer the server's own retryDelay; fall back to exponential."""
    m = re.search(r'retry(?:Delay|[- ]after)"?[:=]\s*"?(\d+(?:\.\d+)?)',
                  msg, re.I) or re.search(r"retry in (\d+(?:\.\d+)?)s", msg, re.I)
    if m:
        return min(float(m.group(1)) + 1.0, 65.0)
    return min(2.0 ** attempt, 60.0)


def _call_with_retry(fn, system: str, user: str, max_tokens: int,
                     json_mode: bool) -> str:
    last: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return fn(system, user, max_tokens, json_mode=json_mode)
        except Exception as e:                              # noqa: BLE001
            msg = str(e)
            if not any(k in msg.lower() for k in _RATE_LIMITED):
                raise
            last = e
            if attempt == _RETRY_ATTEMPTS - 1:
                break
            time.sleep(_retry_after(msg, attempt))
    raise GenerationError(f"rate limited after {_RETRY_ATTEMPTS} attempts: "
                          f"{type(last).__name__}: {last}"[:300]) from last


def available() -> bool:
    p = config.LLM_PROVIDER
    return bool(
        (p == "groq" and config.GROQ_API_KEYS)
        or (p == "anthropic" and config.ANTHROPIC_API_KEY)
        or (p == "gemini" and config.GEMINI_API_KEY)
        or (p == "nvidia" and config.NVIDIA_API_KEY)
        or (p == "ollama")
    )


def provider_label() -> str:
    p = config.LLM_PROVIDER
    model = {
        "groq": config.GROQ_MODEL,
        "anthropic": config.ANTHROPIC_MODEL,
        "gemini": config.GEMINI_MODEL,
        "nvidia": config.NVIDIA_MODEL,
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
        "nvidia": "UNVERIFIED — NVIDIA's build.nvidia.com trial terms were not "
                  "confirmed for this demo. Treat as synthetic data only until "
                  "you have their data-use position in writing.",
        "ollama": "No — runs entirely on this machine, no network egress",
    }.get(config.LLM_PROVIDER, "unknown")


# ------------------------------------------------------------------ backends

def _groq_call(system: str, user: str, max_tokens: int, api_key: str,
               json_mode: bool = False) -> str:
    from groq import Groq
    client = Groq(api_key=api_key)
    r = client.chat.completions.create(
        model=config.GROQ_MODEL,
        max_tokens=max_tokens,
        temperature=0,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return r.choices[0].message.content or ""


def _groq(system: str, user: str, max_tokens: int,
          json_mode: bool = False) -> str:
    """Rotate across GROQ_API_KEYS on a per-key rate limit.

    Each free Groq key has its own 100k-tokens/day budget, so a second key
    is an independent quota, not a fallback provider -- rotation happens
    immediately, with no backoff sleep, since the next key hasn't been
    touched today. Only once every key in the pool has been tried does the
    exception surface to _call_with_retry, whose own backoff-and-retry
    (and then LLM_FALLBACK_PROVIDER) takes over as before.
    """
    if not config.GROQ_API_KEYS:
        raise GenerationError("no GROQ_API_KEY / GROQ_API_KEYS configured")
    last: Exception | None = None
    for key in config.GROQ_API_KEYS:
        try:
            return _groq_call(system, user, max_tokens, key,
                              json_mode=json_mode)
        except Exception as e:                          # noqa: BLE001
            if not any(k in str(e).lower() for k in _RATE_LIMITED):
                raise
            last = e
    raise last


def _anthropic(system: str, user: str, max_tokens: int,
               json_mode: bool = False) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    r = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return r.content[0].text


def _gemini(system: str, user: str, max_tokens: int,
            json_mode: bool = False) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=config.GEMINI_API_KEY)

    def _go(**extra) -> str:
        r = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0,
                **extra,
            ),
        )
        return r.text or ""

    if not json_mode:
        return _go(max_output_tokens=max_tokens)

    # max_output_tokens counts thinking tokens on Gemini 3.x, and these models
    # spend ~1k of them before emitting anything. Left on, the JSON truncates
    # mid-object and never closes its brace, so the parser sees no JSON at all.
    # response_mime_type also drops the ```json fences.
    # thinking_budget=0 is rejected (400) by some models in the family, so fall
    # back to simply giving the thinking room to run without starving the answer.
    try:
        return _go(max_output_tokens=max_tokens,
                   response_mime_type="application/json",
                   thinking_config=types.ThinkingConfig(thinking_budget=0))
    except Exception as e:                                  # noqa: BLE001
        if "INVALID_ARGUMENT" not in str(e):
            raise
        return _go(max_output_tokens=max(max_tokens, 4096),
                   response_mime_type="application/json")


def _nvidia(system: str, user: str, max_tokens: int,
            json_mode: bool = False) -> str:
    # NIM speaks the OpenAI chat API, so the openai SDK drives it unchanged.
    from openai import OpenAI
    client = OpenAI(base_url=config.NVIDIA_BASE_URL,
                    api_key=config.NVIDIA_API_KEY)
    kw = {"response_format": {"type": "json_object"}} if json_mode else {}
    # NIM's reasoning models (nemotron-*) spend max_tokens on thinking before
    # emitting, exactly like Gemini 3.x above. At the 1600 default the JSON
    # truncates mid-object; 4096 leaves room for the answer itself.
    if json_mode:
        max_tokens = max(max_tokens, 4096)
    r = client.chat.completions.create(
        model=config.NVIDIA_MODEL,
        max_tokens=max_tokens,
        temperature=0,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        **kw,
    )
    return r.choices[0].message.content or ""


def _ollama(system: str, user: str, max_tokens: int,
            json_mode: bool = False) -> str:
    import httpx
    body = {
        "model": config.OLLAMA_MODEL,
        "stream": False,
        "options": {"temperature": 0, "num_predict": max_tokens},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    if json_mode:
        body["format"] = "json"
    r = httpx.post(
        f"{config.OLLAMA_HOST}/api/chat",
        timeout=180,
        json=body,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


_BACKENDS = {"groq": _groq, "anthropic": _anthropic,
             "gemini": _gemini, "nvidia": _nvidia, "ollama": _ollama}


def _generate(system: str, user: str, max_tokens: int, json_mode: bool) -> str:
    fn = _BACKENDS.get(config.LLM_PROVIDER)
    if fn is None:
        raise GenerationError(f"Unknown LLM_PROVIDER: {config.LLM_PROVIDER}")
    try:
        return _call_with_retry(fn, system, user, max_tokens, json_mode)
    except GenerationError:
        # _call_with_retry only raises GenerationError once the primary's own
        # retries are exhausted on a rate-limit-shaped error -- anything else
        # (auth, malformed request) propagates as its native exception and
        # skips the fallback, since a second provider won't fix a bad key.
        fallback = config.LLM_FALLBACK_PROVIDER
        ffn = _BACKENDS.get(fallback) if fallback else None
        if ffn is None or fallback == config.LLM_PROVIDER:
            raise
        return _call_with_retry(ffn, system, user, max_tokens, json_mode)


def generate_text(system: str, user: str, *, max_tokens: int = 120) -> str:
    return (_generate(system, user, max_tokens, False) or "").strip()


def generate_json(system: str, user: str, *, max_tokens: int = 1600) -> dict:
    text = (_generate(system, user, max_tokens, True) or "").strip()
    if not text:
        raise GenerationError("empty response")

    # Models that ignore json_mode still wrap the object in ```json fences.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise GenerationError(f"no JSON in response: {text[:180]}") from None
        return json.loads(m.group(0))
