from __future__ import annotations

import json
import re
from typing import Any


def extract_json(text: str) -> Any:
    if not isinstance(text, str):
        return {}
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
        t = t.strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, (dict, list)) else {}
    except Exception:
        pass
    match = re.search(r"\[.*\]", t, re.DOTALL)
    if not match:
        match = re.search(r"\{.*\}", t, re.DOTALL)
    if not match:
        return {}
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, (dict, list)) else {}
    except Exception:
        return {}


def extract_output_text(data: dict) -> str:
    # Responses API (output)
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "output_text":
                    text = block.get("text", "")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
    if isinstance(output, dict):
        try:
            content = output["choices"][0]["message"]["content"]
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        text = block.get("text", "")
                        if isinstance(text, str) and text.strip():
                            return text.strip()
        except Exception:
            pass

    # Chat Completions API (choices[0].message.content)
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        t = block.get("text")
                        if isinstance(t, str) and t.strip():
                            return t.strip()

    return ""

