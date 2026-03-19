from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from .prompt_templates import get_prompt_content
from .ai_engine_runtime import vision_recognize_with_engine

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

    api_key = get_secret_setting(setting_key="volcengine_api_key", env_key="VOLCENGINE_API_KEY")
    if not api_key:
        raise RuntimeError("AI 未配置：请设置环境变量 VOLCENGINE_API_KEY，然后在「设置 → AI 设置」选择引擎。")
    raise RuntimeError("未配置图片识别引擎：请在「设置 → AI 设置 → 按能力选择引擎」选择“图片识别”引擎。")

