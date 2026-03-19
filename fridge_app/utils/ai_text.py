from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import requests


from .ark_config import build_ark_endpoint, get_active_ark_profile_for, get_timeout_seconds, merged_request_params
from .ai_parse import extract_json as ark_extract_json, extract_output_text as ark_extract_output_text
from .prompt_templates import get_prompt_content
from ..services.settings_service import get_category_options, get_location_options
from .ai_engine_runtime import text_extract_with_engine

PROMPT_TEXT_TO_ITEMS = """
你是家庭食材物品识别助手。
请从用户输入的自然语言中提取“所有提到的物品”，包括吃了什么、买了什么、拿出来用了什么等。

【返回格式要求（非常重要）】
1) 只返回严格的 JSON 数组（list），不要输出任何额外文字、解释、Markdown；
2) 每个物品是一个 JSON 对象，字段必须包含且仅包含：
   - name: 名称（string）
   - number: 数量（int，识别不到填 1；如果明确说“吃了/用完了”，数量可以填 0 但仍需输出该物品）
   - category: 分类（string，必须严格从【分类/位置 选项（必须严格遵守）】里的“分类列表”中选择；不在列表内则返回“其他”）
   - location: 存放位置（string，必须严格从【分类/位置 选项（必须严格遵守）】里的“位置列表”中选择；不在列表内则返回空字符串）
   - status: 状态（可选，string，例如：已吃/已用完/新购/开封/未开封；识别不到可省略或空字符串）
3) 如果没有任何物品，返回空数组：[]

示例输出：
[
  {"name":"苹果","number":1,"category":"水果","location":"冰箱","status":"已吃"},
  {"name":"牛奶","number":1,"category":"饮品","location":"冰箱","status":"新购"}
]
""".strip()


def _env_on(name: str, default: str = "1") -> bool:
    raw = (os.environ.get(name) or default).strip()
    return raw in {"1", "true", "True", "YES", "yes", "on", "ON"}


def _extract_output_text(data: dict) -> str:
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
                    t = block.get("text", "")
                    if isinstance(t, str) and t.strip():
                        return t.strip()
    if isinstance(output, dict):
        try:
            content = output["choices"][0]["message"]["content"]
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        t = block.get("text", "")
                        if isinstance(t, str) and t.strip():
                            return t.strip()
        except Exception:
            pass
    return ""


def _extract_json(text: str) -> Any:
    if not isinstance(text, str):
        return []
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
        t = t.strip()
    try:
        obj = json.loads(t)
        return obj
    except Exception:
        pass
    m = re.search(r"\[.*\]", t, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except Exception:
        return []


def _safe_int(v: Any, default: int = 1) -> int:
    try:
        s = str(v).strip()
        if s == "":
            return default
        return int(float(s.replace(",", "")))
    except Exception:
        return default


def extract_items_from_text(text: str) -> list[dict[str, Any]]:
    from ..services.settings_service import get_secret_setting

    # Prefer AiModel engine if configured
    text_prompt = str(get_prompt_content("text_extract", default_content=PROMPT_TEXT_TO_ITEMS))
    engine_any = text_extract_with_engine(text, prompt=text_prompt)
    if engine_any is not None:
        items_any = engine_any if isinstance(engine_any, list) else (engine_any or [])
        if not isinstance(items_any, list):
            return []
        # reuse downstream normalization
        items_list = items_any
        allowed_categories = get_category_options()
        if "其他" not in allowed_categories:
            allowed_categories.append("其他")
        allowed_locations = get_location_options()

        out: list[dict[str, Any]] = []
        for it in items_list:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            if not name:
                continue
            number = _safe_int(it.get("number", 1), default=1)
            category = str(it.get("category") or "其他").strip() or "其他"
            if category not in allowed_categories:
                category = "其他"
            location = str(it.get("location") or "").strip()
            if location and location not in allowed_locations:
                location = ""
            status = str(it.get("status") or "").strip()
            out.append(
                {
                    "name": name[:80],
                    "quantity": max(0, number),
                    "category": category,
                    "location": location[:40],
                    "status": status[:40],
                }
            )
        return out

    profile = get_active_ark_profile_for("text_extract")
    endpoint = build_ark_endpoint(profile)
    model = str(profile.get("model") or "")
    api_type = str(profile.get("api_type") or "responses").strip()
    base_url = str(profile.get("base_url") or "")
    if api_type not in {"responses", "chat_completions"}:
        raise RuntimeError(f"[AI] text_extract 仅支持 responses/chat_completions，当前 api_type={api_type}")
    text_prompt = str(profile.get("text_prompt") or text_prompt)
    extra_params = merged_request_params(profile)
    for k in ["model", "input", "messages"]:
        extra_params.pop(k, None)

    api_key = get_secret_setting(setting_key="volcengine_api_key", env_key="VOLCENGINE_API_KEY")
    if not api_key:
        raise RuntimeError("VOLCENGINE_API_KEY missing (set env var or save it in 设置页)")

    verbose = _env_on("AI_LOG_VERBOSE", default="1")
    full_response = _env_on("AI_LOG_FULL_RESPONSE", default="1")

    user_tail = f"用户输入：{text}"
    if api_type == "chat_completions":
        full_prompt = f"{text_prompt}\n\n{user_tail}"
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": full_prompt,
                }
            ],
        }
    else:
        payload = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": text_prompt},
                        {"type": "input_text", "text": user_tail},
                    ],
                }
            ],
        }

    if extra_params:
        payload.update(extra_params)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    start = time.time()
    timeout_s = get_timeout_seconds(profile, default=60)
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout_s)
    cost_ms = int((time.time() - start) * 1000)

    if verbose:
        print("=" * 80)
        print("[AI] Text parse response")
        print("status:", resp.status_code, "cost_ms:", cost_ms)
        try:
            body = resp.json()
            if full_response:
                print(json.dumps(body, ensure_ascii=False, indent=2))
            else:
                print(json.dumps(body, ensure_ascii=False))
        except Exception:
            print(resp.text or "")
        print("=" * 80)

    resp.raise_for_status()
    data = resp.json()

    out_text = ark_extract_output_text(data)
    items_any = ark_extract_json(out_text)
    if not isinstance(items_any, list):
        return []

    allowed_categories = get_category_options()
    if "其他" not in allowed_categories:
        allowed_categories.append("其他")
    allowed_locations = get_location_options()

    out: list[dict[str, Any]] = []
    for it in items_any:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        number = _safe_int(it.get("number", 1), default=1)
        category = str(it.get("category") or "其他").strip() or "其他"
        if category not in allowed_categories:
            category = "其他"
        location = str(it.get("location") or "").strip()
        if location and location not in allowed_locations:
            location = ""
        status = str(it.get("status") or "").strip()
        out.append(
            {
                "name": name[:80],
                "quantity": max(0, number),
                "category": category,
                "location": location[:40],
                "status": status[:40],
            }
        )
    return out

