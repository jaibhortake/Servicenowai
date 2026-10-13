"""
Form Prefill Service.

Maps a user's natural-language request to catalog form variable values
using the LLM. The LLM sees the user query plus the variable schema
(labels, types, choices) and returns a JSON object with values that
can safely be applied to the form. Output is post-validated:
  - select/radio values must be in the variable's choices
  - checkbox values must be booleans
  - text/textarea values must be non-empty strings

On any failure or low confidence, returns {} (silent fallback).
"""

import json
import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.ai_service import get_async_client

logger = logging.getLogger(__name__)


PREFILL_PROMPT = """You are an enterprise IT service catalog assistant.
A user submitted this request: "{query}"

{catalog_context}
{intent_context}

Below is the schema of catalog form variables they need to fill:
{schema}

For each variable, decide if the user's request implies a value based on the context.
Return a JSON object mapping variable "key" to value.
Keys may be either the variable's sys_id or its field name.

Strict rules:
- For type "select" or "radio": value MUST be exactly one of the listed choice values.
  If no choice fits, omit the key.
- For type "checkbox": value must be true or false. Only include if request explicitly
  implies it. Omit otherwise.
- For type "text" or "textarea": short value extracted from the request. Omit if nothing
  in the request applies to this variable.
- For type "date" or "datetime": only fill if an explicit date is mentioned.
- Do NOT invent information that isn't in the request.
- It is fine to return an empty object {{}} if the request implies nothing.

Return ONLY a valid JSON object, no markdown, no commentary.
Example: {{"<key1>": "<choice_value>", "<key2>": true}}
"""


async def prefill_form(
    query: str,
    variables: List[Dict[str, Any]],
    catalog_item: Optional[Dict[str, Any]] = None,
    intent_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Map a user query to catalog form variable values via LLM.

    Args:
        query: User's natural language request
        variables: Form variables schema
        catalog_item: Selected catalog item (name, description, category)
        intent_context: Intent classification (intent, confidence, entities)

    Returns:
        Dict keyed by variable sys_id (or name when sys_id missing) → value.
        Empty dict on any failure.
    """
    if not query or not query.strip() or not variables:
        return {}

    schema_lines: List[Dict[str, Any]] = []
    spec_by_key: Dict[str, Dict[str, Any]] = {}

    for v in variables:
        key = (v.get("sys_id") or v.get("name") or "").strip()
        if not key:
            continue
        line: Dict[str, Any] = {
            "key": key,
            "name": v.get("name") or "",
            "label": v.get("label") or v.get("name") or "",
            "type": (v.get("type") or "text").lower(),
        }
        choices = v.get("choices") or []
        if choices:
            line["choices"] = [
                {"label": c.get("label"), "value": c.get("value")}
                for c in choices
                if c.get("value") is not None
            ]
        schema_lines.append(line)
        spec = {"type": line["type"], "choices": line.get("choices") or []}
        spec_by_key[key] = spec
        if line["name"] and line["name"] != key:
            spec_by_key[line["name"]] = spec

    if not schema_lines:
        return {}

    catalog_context = ""
    if catalog_item and catalog_item.get("name"):
        catalog_context = f"The selected catalog item is: {catalog_item.get('name')}\nDescription: {catalog_item.get('short_description', catalog_item.get('description', ''))}"

    intent_context_str = ""
    if intent_context:
        intent = intent_context.get("intent", "")
        entities = intent_context.get("entities", [])
        if intent:
            intent_context_str = f"User intent: {intent}"
            if entities:
                intent_context_str += f"\nKey entities mentioned: {', '.join(entities)}"

    prompt = PREFILL_PROMPT.format(
        query=query,
        catalog_context=catalog_context,
        intent_context=intent_context_str,
        schema=json.dumps(schema_lines, indent=2),
    )

    try:
        async_client = get_async_client()
        completion = await async_client.chat.completions.create(
            model=settings.openai_chat_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an enterprise IT service catalog assistant. Return ONLY a valid JSON object.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=400,
        )

        raw = (completion.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[PREFILL] Non-JSON response: {raw[:200]}")
            return {}

        if not isinstance(parsed, dict):
            return {}

        cleaned: Dict[str, Any] = {}
        for key, value in parsed.items():
            spec = spec_by_key.get(key)
            if not spec:
                continue
            vtype = spec["type"]
            choices = spec["choices"]

            if vtype in ("select", "radio"):
                allowed = {str(c.get("value")) for c in choices}
                if not allowed or str(value) not in allowed:
                    continue
                cleaned[key] = str(value)
            elif vtype == "checkbox":
                if not isinstance(value, bool):
                    continue
                cleaned[key] = value
            elif vtype in ("date", "datetime"):
                if not isinstance(value, str) or not value.strip():
                    continue
                cleaned[key] = value.strip()
            else:
                if value is None:
                    continue
                if isinstance(value, str):
                    if not value.strip():
                        continue
                    cleaned[key] = value.strip()
                else:
                    cleaned[key] = str(value)

        logger.info(f"[PREFILL] Mapped {len(cleaned)}/{len(parsed)} fields from query='{query[:60]}'")
        return cleaned

    except Exception as e:
        logger.exception(f"[PREFILL] Error: {e}")
        return {}
