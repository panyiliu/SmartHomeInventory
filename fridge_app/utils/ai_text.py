from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import requests


from .prompt_templates import get_prompt_content
from ..services.settings_service import get_category_options, get_location_options, normalize_icon_spec
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


PROMPT_ICON_SUGGEST = """
你是家庭食材物品图标生成助手。
你将收到输入 JSON，其中包含若干条“分类/位置名称”（用字符串表示）。
你的任务：为每个名称生成最合适的图标候选（用于系统 icon spec）。

【返回格式要求（非常重要）】
1) 只返回严格 JSON 数组（list），不要输出任何额外文字、解释、Markdown；
2) 每个元素为对象，字段必须包含且仅包含：
   - name: 名称（string）
   - icon_candidates: 图标候选数组（list[string]）
3) icon_candidates 里每个候选必须是以下格式之一（严格遵守）：
   - "emoji:<emoji>" 例如 "emoji:🥬"
   - "bi:<bootstrap-icon-name>" 例如 "bi:box"
4) icon_candidates 长度必须为候选数量 N（N 由输入给出）。
   如果你无法给出足够候选，请重复最合适的那个候选以凑满 N。

【约束】
- 不要输出其它字段；
- 保证 JSON 可被直接 json.loads() 解析。
""".strip()


def _stored_icon_spec_from_candidate(candidate: str) -> str:
    """
    Convert AI output to the stored icon spec string:
    - emoji:<text>
    - bi:<name>
    - svg:<key>
    - '' if invalid/empty
    """
    norm = normalize_icon_spec(candidate)
    t = norm.get("type")
    if t == "none":
        return ""
    if t == "emoji":
        text = str(norm.get("text") or "").strip()
        return f"emoji:{text}" if text else ""
    if t == "bi":
        name = str(norm.get("name") or "").strip()
        return f"bi:{name}" if name else ""
    if t == "svg":
        key = str(norm.get("key") or "").strip()
        return f"svg:{key}" if key else ""
    return ""


def generate_icon_candidates_for_names(
    names: list[str],
    *,
    kind: str,
    candidates_per_item: int = 3,
) -> dict[str, list[str]]:
    """
    Use AI to generate icon candidates for missing category/location keys.
    Returns: { "<name>": ["emoji:...", "bi:...", ...] } (each list length == candidates_per_item).
    """
    cleaned: list[str] = []
    for n in names or []:
        s = str(n or "").strip()
        if s:
            cleaned.append(s)
    cleaned = cleaned[:50]

    n = int(candidates_per_item or 3)
    n = max(1, min(n, 5))

    kind_norm = str(kind).strip().lower()
    kind_label = "分类" if kind_norm in {"category", "cat"} else "位置"

    # Note: we use a custom prompt; do NOT rely on the normal get_prompt_content injection.
    user_text = json.dumps(
        {"kind": kind_norm, "kind_label": kind_label, "items": cleaned, "candidates": n},
        ensure_ascii=False,
    )
    engine_any = text_extract_with_engine(user_text, prompt=PROMPT_ICON_SUGGEST)
    if engine_any is None:
        return {}

    items_any = engine_any if isinstance(engine_any, list) else (engine_any or [])
    if not isinstance(items_any, list):
        return {}

    out: dict[str, list[str]] = {}
    for obj in items_any:
        if not isinstance(obj, dict):
            continue
        name = str(obj.get("name") or "").strip()
        if not name:
            continue
        cands_any = obj.get("icon_candidates")
        if not isinstance(cands_any, list):
            continue

        cands_normed: list[str] = []
        for c in cands_any:
            spec = _stored_icon_spec_from_candidate(str(c or "").strip())
            if spec and spec not in cands_normed:
                cands_normed.append(spec)
            if len(cands_normed) >= n:
                break
        if not cands_normed:
            continue

        # Pad to exact length for stable client behavior.
        while len(cands_normed) < n:
            cands_normed.append(cands_normed[0])
        out[name] = cands_normed[:n]

    return out


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

    # No legacy ark_profiles fallback: self-host version uses AiModel engines only.
    api_key = get_secret_setting(setting_key="volcengine_api_key", env_key="VOLCENGINE_API_KEY")
    if not api_key:
        raise RuntimeError("AI 未配置：请设置环境变量 VOLCENGINE_API_KEY，然后在「设置 → AI 设置」选择引擎。")
    raise RuntimeError("未配置文本解析引擎：请在「设置 → AI 设置 → 按能力选择引擎」选择“文本解析”引擎。")

