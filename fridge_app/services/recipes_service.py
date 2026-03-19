from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Tuple

from ..models import Item
from ..services.settings_service import get_setting, get_secret_setting
from ..utils.ai_engine_runtime import recipes_generate_with_engine
import json
import os
import requests


@dataclass
class RecipeStep:
    text: str


@dataclass
class Recipe:
    name: str
    ingredients: List[str]
    steps: List[RecipeStep]
    match_count: int
    total_count: int


def get_available_ingredients(now: datetime | None = None) -> list[dict[str, Any]]:
    """查询当前可用食材：未用完、数量>0，且未过期。"""
    now = now or datetime.utcnow()
    items = Item.query.filter_by(used_up=False).all()
    result: list[dict[str, Any]] = []
    for it in items:
        if it.quantity <= 0:
            continue
        remain = it.remaining_days(now=now)
        if remain is not None and remain < 0:
            continue
        result.append(
            {
                "name": it.name,
                "quantity": float(it.quantity),
                "unit": it.unit,
                "location": it.location,
            }
        )
    return result


def _default_prompt_template() -> str:
    return (
        "你是一个中文家常菜食谱推荐助手。请根据用户冰箱中的食材，推荐 3~5 道适合家庭晚餐的菜谱。\n\n"
        "【可用食材列表】：\n"
        "{{ingredients}}\n\n"
        "要求：\n"
        "1. 优先使用以上食材，尽量减少浪费；考虑食材数量，不要推荐明显超出现有食材数量的菜。\n"
        "2. 每道菜返回：\n"
        "   - name：菜名\n"
        "   - ingredients：所需食材及大致用量（字符串数组）\n"
        "   - steps：3~8 步简明的烹饪步骤（字符串数组）\n\n"
        "请严格以 JSON 格式返回，格式示例：\n"
        "[\n"
        "  {\n"
        '    \"name\": \"示例菜名\",\n'
        '    \"ingredients\": [\"食材1 x数量\", \"食材2 x数量\"],\n'
        '    \"steps\": [\"步骤1\", \"步骤2\"]\n'
        "  }\n"
        "]\n\n"
        "只返回 JSON，不要任何额外解释或文字。"
    )


def build_recipe_prompt(ingredients: list[dict[str, Any]]) -> str:
    lines = []
    if not ingredients:
        lines.append("- 暂无可用食材")
    else:
        for it in ingredients:
            name = str(it.get("name", "") or "")[:50]
            qty = it.get("quantity")
            unit = str(it.get("unit", "") or "")
            parts = [name]
            if qty is not None:
                try:
                    parts.append(f"{float(qty):g}")
                except Exception:
                    pass
            if unit:
                parts.append(unit)
            lines.append("- " + " ".join(parts))
    ing_block = "\n".join(lines)

    tmpl = get_setting("recipe_prompt_template", "").strip() or _default_prompt_template()
    return tmpl.replace("{{ingredients}}", ing_block)


def _call_ark_text(prompt: str, *, user_text: str = "") -> Any:
    """通过引擎生成菜谱（不再支持 legacy ark_profiles）。"""
    engine_obj = recipes_generate_with_engine(prompt, user_text=user_text)
    if engine_obj is not None:
        return engine_obj

    api_key = get_secret_setting(setting_key="volcengine_api_key", env_key="VOLCENGINE_API_KEY")
    if not api_key:
        raise RuntimeError("AI 未配置：请设置环境变量 VOLCENGINE_API_KEY，然后在「设置 → AI 设置」选择引擎。")
    raise RuntimeError("未配置菜谱生成引擎：请在「设置 → AI 设置 → 按能力选择引擎」选择“菜谱生成”引擎。")


def _to_recipes(obj: Any, ingredients: list[dict[str, Any]]) -> list[Recipe]:
    if not isinstance(obj, list):
        return []

    # 方便匹配：库存食材名小写去空格
    stock_names = [str(it.get("name", "")).strip().lower() for it in ingredients]

    recipes: list[Recipe] = []
    for item in obj:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        ing_list_raw = item.get("ingredients") or []
        steps_raw = item.get("steps") or []

        ing_list: list[str] = []
        if isinstance(ing_list_raw, list):
            for x in ing_list_raw:
                t = str(x or "").strip()
                if t:
                    ing_list.append(t)

        steps: list[RecipeStep] = []
        if isinstance(steps_raw, list):
            for x in steps_raw:
                t = str(x or "").strip()
                if t:
                    steps.append(RecipeStep(text=t))

        if not ing_list or not steps:
            continue

        # 简单匹配度统计：按原始名称前缀匹配
        match = 0
        for ing in ing_list:
            ing_name = str(ing).split()[0].strip().lower()
            if ing_name and any(ing_name in s or s in ing_name for s in stock_names):
                match += 1

        recipes.append(
            Recipe(
                name=name[:80],
                ingredients=ing_list,
                steps=steps,
                match_count=match,
                total_count=len(ing_list),
            )
        )

    return recipes


def generate_recipes(*, user_text: str = "") -> Tuple[list[Recipe], str | None]:
    """
    主入口：根据库存生成菜谱列表。
    返回 (recipes, error)；error 非 None 表示失败。
    """
    ingredients = get_available_ingredients()
    if not ingredients:
        return [], None
    prompt = build_recipe_prompt(ingredients)
    verbose = (os.environ.get("RECIPE_LOG_VERBOSE") or "1").strip() not in {"0", "false", "False", "OFF", "off"}
    if verbose:
        print("[RECIPES] prompt_chars:", len(prompt))
    try:
        obj = _call_ark_text(prompt, user_text=user_text)
    except RuntimeError as e:
        return [], str(e)
    except Exception as e:
        return [], f"AI 调用失败：{e}"

    recipes = _to_recipes(obj, ingredients)
    return recipes, None

