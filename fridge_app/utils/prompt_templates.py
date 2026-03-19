from __future__ import annotations

from typing import Optional

from ..models import AiPromptTemplate
from ..services.settings_service import get_category_options, get_location_options


def get_prompt_by_category(category_code: str) -> Optional[AiPromptTemplate]:
    if not category_code:
        return None
    return (
        AiPromptTemplate.query.filter_by(category_code=category_code)
        .order_by(AiPromptTemplate.is_default.desc(), AiPromptTemplate.updated_at.desc())
        .first()
    )


def get_prompt_content(category_code: str, *, default_content: str = "") -> str:
    row = get_prompt_by_category(category_code)
    base = row.content if (row and row.content) else default_content
    base = str(base or "")

    # Global option injection (strict mode):
    # Keep AI outputs aligned with admin-configured option lists.
    if category_code in {"vision_recognize", "text_extract"}:
        cats = get_category_options()
        locs = get_location_options()
        if "其他" not in cats:
            cats = [*cats, "其他"]

        cats_str = "、".join(cats)
        locs_str = "、".join(locs)

        guard = (
            "\n\n【分类/位置 选项（必须严格遵守）】\n"
            f"- 分类（category）只能从以下列表中选择：{cats_str}；否则返回“其他”。\n"
            f"- 位置（location）只能从以下列表中选择：{locs_str}；否则返回空字符串。\n"
        )

        # If user already pasted a similar block, avoid duplicating too much.
        if "【分类/位置" not in base:
            base = base.rstrip() + guard

    return base

