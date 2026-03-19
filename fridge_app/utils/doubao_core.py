from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from typing import Any

import requests


from .ark_config import (
    build_ark_endpoint,
    get_active_ark_profile_for,
    get_timeout_seconds,
    merged_request_params,
)
from .prompt_templates import get_prompt_content
from .ai_engine_runtime import vision_recognize_with_engine
from .ai_parse import extract_json, extract_output_text
ALLOWED_TYPES = ["调味品", "食材", "蔬菜", "水果", "饮品", "零食", "日用品", "其他"]

FIXED_PROMPT = """
你是家庭食材物品识别助手。
请识别图片中的所有物品，尽量完整，不要遗漏明显的物品。

【返回格式要求（非常重要）】
1) 只返回严格的 JSON 数组（list），不要输出任何额外文字、解释、Markdown、标点；
2) 每个物品是一个 JSON 对象，字段必须包含且仅包含：
   - name: 名称（string）
   - number: 数量（int，识别不到填 0）
   - category: 分类（string，必须严格从【分类/位置 选项（必须严格遵守）】里的“分类列表”中选择；不在列表内则返回“其他”）
   - location: 存放位置（string，必须严格从【分类/位置 选项（必须严格遵守）】里的“位置列表”中选择；不在列表内则返回空字符串）
   - status: 状态（可选，string，例如：开封/未开封/临期/正常；识别不到可省略或空字符串）
3) 如果图片中没有任何物品，返回空数组：[]

示例输出：
[
  {"name":"西红柿","number":1,"category":"蔬菜","location":"冰箱"},
  {"name":"鸡蛋","number":2,"category":"食材","location":"冰箱"}
]
""".strip()


@dataclass
class ItemResult:
    物品名称: str = ""
    数量: int = 0
    类型: str = ""
    生产日期: str = ""
    保质期: int = 0
    过期日期: str = ""

    @classmethod
    def from_ai(cls, data: dict[str, Any] | None) -> "ItemResult":
        if not isinstance(data, dict):
            return cls()

        name = str(data.get("物品名称", ""))[:50]

        def safe_int(v: Any) -> int:
            try:
                s = str(v).strip()
                if s == "":
                    return 0
                return int(float(s.replace(",", "")))
            except Exception:
                return 0

        qty = safe_int(data.get("数量", 0))
        shelf = safe_int(data.get("保质期", 0))

        item_type = data.get("类型", "")
        if item_type not in ALLOWED_TYPES:
            item_type = ""

        prod_date = str(data.get("生产日期", "")).strip()
        expire_date = str(data.get("过期日期", "")).strip()

        return cls(
            物品名称=name,
            数量=qty,
            类型=item_type,
            生产日期=prod_date,
            保质期=shelf,
            过期日期=expire_date,
        )


def _env_on(name: str, default: str = "1") -> bool:
    raw = (os.environ.get(name) or default).strip()
    return raw in {"1", "true", "True", "YES", "yes", "on", "ON"}


#
# NOTE: extract_json / extract_output_text moved to `ai_parse.py`
#


def call_ark_vision(image_bytes: bytes) -> Any:
    """
    Call Ark (Responses / Chat Completions) with an image and return extracted JSON.
    Requires VOLCENGINE_API_KEY (env preferred; settings fallback supported).
    """
    from ..services.settings_service import get_secret_setting

    # Prefer AiModel engine if configured
    vision_prompt = str(get_prompt_content("vision_recognize", default_content=FIXED_PROMPT))
    engine_out = vision_recognize_with_engine(image_bytes, prompt=vision_prompt)
    if engine_out is not None:
        return engine_out

    profile = get_active_ark_profile_for("vision_recognize")
    endpoint = build_ark_endpoint(profile)
    model = str(profile.get("model") or "")
    api_type = str(profile.get("api_type") or "responses").strip()
    base_url = str(profile.get("base_url") or "")
    if api_type not in {"responses", "chat_completions"}:
        raise RuntimeError(f"[AI] vision_recognize 仅支持 responses/chat_completions，当前 api_type={api_type}")
    vision_prompt = str(profile.get("vision_prompt") or vision_prompt)
    extra_params = merged_request_params(profile)
    # Never allow overriding core fields accidentally.
    for k in ["model", "input", "messages"]:
        extra_params.pop(k, None)

    api_key = get_secret_setting(setting_key="volcengine_api_key", env_key="VOLCENGINE_API_KEY")
    if not api_key:
        raise RuntimeError("VOLCENGINE_API_KEY missing (set env var or save it in 设置页)")

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    image_data_url = f"data:image/jpeg;base64,{image_b64}"

    if api_type == "chat_completions":
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url},
                        },
                        {"type": "text", "text": vision_prompt},
                    ],
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
                        {"type": "input_image", "image_url": image_data_url},
                        {"type": "input_text", "text": vision_prompt},
                    ],
                }
            ],
        }

    if extra_params:
        payload.update(extra_params)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    verbose = _env_on("AI_LOG_VERBOSE", default="1")
    full_response = _env_on("AI_LOG_FULL_RESPONSE", default="1")
    timeout_s = get_timeout_seconds(profile, default=60)
    start = time.time()
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout_s)
    cost_ms = int((time.time() - start) * 1000)

    if verbose:
        print("=" * 80)
        print("[AI] Ark vision response")
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
    out_text = extract_output_text(data)
    return extract_json(out_text) if out_text else {}

