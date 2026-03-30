"""AI-assisted lyric replacement using OpenAI-compatible chat APIs."""

from __future__ import annotations

import json
import re
from urllib import error, request


def _extract_json_array(text: str) -> list[str]:
    s = (text or "").strip()
    if not s:
        raise ValueError("AI response was empty")
    # Try full JSON first
    try:
        obj = json.loads(s)
        if isinstance(obj, list):
            return [str(x) for x in obj]
    except json.JSONDecodeError:
        pass
    # Fallback: first JSON array in content
    m = re.search(r"\[[\s\S]*\]", s)
    if not m:
        raise ValueError("AI response did not contain a JSON array")
    obj = json.loads(m.group(0))
    if not isinstance(obj, list):
        raise ValueError("AI response JSON is not an array")
    return [str(x) for x in obj]


def _build_prompt(reference_text: str, generated_words: list[str]) -> tuple[str, str]:
    n = len(generated_words)
    system = (
        "You are a lyrics alignment and correction engine. "
        f"The input has EXACTLY {n} note slots. "
        "You MUST return ONLY valid JSON: one array of strings with EXACTLY "
        f"{n} elements — same count as input_words, same order of indices 0..{n - 1}. "
        "Never merge slots, never skip slots, never output fewer or more strings. "
        "Each output string is one UltraStar note line (fragment may include trailing ~). "
        "If an input item is only a tilde marker like '~' or '~~', keep it unchanged. "
        "Preserve trailing '~' if present on an input item. "
        "Do not add explanations, markdown, or code fences."
    )
    user = json.dumps(
        {
            "task": "Replace/fix input_words to best match reference while keeping every slot.",
            "required_slot_count": n,
            "reference": reference_text,
            "input_words": generated_words,
            "required_output": f"JSON array of exactly {n} strings (no other keys or text).",
        },
        ensure_ascii=False,
    )
    return system, user


def _normalize_ai_output_to_slot_count(
    out: list[str], original: list[str]
) -> tuple[list[str], str | None]:
    """If the model returns the wrong length, repair so the file note count stays valid."""
    n = len(original)
    if len(out) == n:
        return out, None
    if len(out) > n:
        return (
            out[:n],
            f"AI returned {len(out)} strings; trimmed to {n} to match note count.",
        )
    missing = n - len(out)
    return (
        out + original[len(out) :],
        f"AI returned {len(out)} strings; kept original text for last {missing} note slot(s).",
    )


def ai_replace_words(
    *,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None,
    reference_text: str,
    generated_words: list[str],
    timeout_seconds: int = 90,
) -> tuple[list[str], str | None]:
    provider_norm = (provider or "").strip().lower()
    if provider_norm not in {"openai", "selfhost"}:
        raise ValueError("provider must be 'openai' or 'selfhost'")
    if not model.strip():
        raise ValueError("model is required")
    if not api_key.strip():
        raise ValueError("api_key is required")
    if not generated_words:
        return [], None

    if provider_norm == "openai":
        endpoint = "https://api.openai.com/v1/chat/completions"
    else:
        b = (base_url or "").strip().rstrip("/")
        if not b:
            raise ValueError("base_url is required for selfhost provider")
        endpoint = b + "/v1/chat/completions"

    system, user = _build_prompt(reference_text, generated_words)
    payload = {
        "model": model.strip(),
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key.strip()}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ValueError(f"AI request failed: HTTP {e.code} {body[:300]}") from e
    except error.URLError as e:
        raise ValueError(f"AI request failed: {e.reason}") from e

    try:
        j = json.loads(raw)
        content = (
            j.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
    except (json.JSONDecodeError, AttributeError, IndexError) as e:
        raise ValueError("AI response was not valid chat-completions JSON") from e

    out = _extract_json_array(str(content))
    fixed, warning = _normalize_ai_output_to_slot_count(out, generated_words)
    return fixed, warning
