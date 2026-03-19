from __future__ import annotations

from datetime import datetime
from pathlib import Path

from flask import Flask

from .config import load_config
from .extensions import db
from .models import Setting
from .models import AiModel, AiPromptTemplate
import json
from .routes.admin import bp as admin_bp
from .routes.items import bp as items_bp
from .routes.main import bp as main_bp
from .routes.recipes import bp as recipes_bp
from .routes.ai_models import bp as ai_models_bp
from .routes.ai_prompts import bp as ai_prompts_bp
from .services.db_migration import ensure_schema
from .utils.doubao_core import FIXED_PROMPT as VISION_DEFAULT_PROMPT
from .utils.ai_text import PROMPT_TEXT_TO_ITEMS as TEXT_DEFAULT_PROMPT
from .services.settings_service import (
    DEFAULT_CATEGORY_ICON_MAP,
    DEFAULT_LOCATION_ICON_MAP,
    DEFAULT_CATEGORY_SHORT_LABEL_MAP,
    DEFAULT_LOCATION_SHORT_LABEL_MAP,
    dump_json_object,
    dump_option_list,
)


def create_app() -> Flask:
    cfg = load_config()

    app = Flask(
        __name__,
        template_folder=str(cfg.base_dir / "templates"),
        instance_path=str(cfg.instance_dir),
        instance_relative_config=True,
    )
    app.config["SECRET_KEY"] = cfg.secret_key
    app.config["SQLALCHEMY_DATABASE_URI"] = cfg.database_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    with app.app_context():
        db.create_all()
        ensure_schema(db)

        def ensure_setting_if_empty(key: str, value: str) -> None:
            row = db.session.get(Setting, key)
            if row is None:
                db.session.add(Setting(key=key, value=value, updated_at=datetime.utcnow()))
                return
            cur = (row.value or "").strip()
            if not cur or cur == "{}":
                row.value = value
                row.updated_at = datetime.utcnow()

        if db.session.get(Setting, "expiring_soon_days") is None:
            db.session.add(Setting(key="expiring_soon_days", value="3", updated_at=datetime.utcnow()))
        if db.session.get(Setting, "barcode_app_id") is None:
            db.session.add(Setting(key="barcode_app_id", value="", updated_at=datetime.utcnow()))
        if db.session.get(Setting, "barcode_app_secret") is None:
            db.session.add(Setting(key="barcode_app_secret", value="", updated_at=datetime.utcnow()))

        # Seed default "数据与选项" on fresh DBs (or when user left them empty as {}).
        ensure_setting_if_empty("category_options", dump_option_list(["蔬菜", "水果", "肉类", "海鲜", "蛋奶", "主食", "调料", "饮料", "零食", "日用品", "其他"]))
        ensure_setting_if_empty("location_options", dump_option_list(["冰箱", "橱柜", "厨房", "室外", "卫生间", "大卧室", "小卧室"]))
        ensure_setting_if_empty("category_icon_map_json", dump_json_object(DEFAULT_CATEGORY_ICON_MAP))
        ensure_setting_if_empty("location_icon_map_json", dump_json_object(DEFAULT_LOCATION_ICON_MAP))
        ensure_setting_if_empty("category_label_map_json", dump_json_object(DEFAULT_CATEGORY_SHORT_LABEL_MAP))
        ensure_setting_if_empty("location_label_map_json", dump_json_object(DEFAULT_LOCATION_SHORT_LABEL_MAP))
        db.session.commit()

        # One-time migration: rename ark_api_key -> volcengine_api_key (strict naming).
        if db.session.get(Setting, "volcengine_api_key") is None:
            old = db.session.get(Setting, "ark_api_key")
            if old and (old.value or "").strip():
                db.session.add(Setting(key="volcengine_api_key", value=old.value, updated_at=datetime.utcnow()))
                db.session.delete(old)
                db.session.commit()

        # Seed default prompt templates (so the backend invocation stays consistent and editable later).
        vision_default_prompt = VISION_DEFAULT_PROMPT
        text_default_prompt = TEXT_DEFAULT_PROMPT

        interface_test_default_prompt = "你好，请回复“测试成功”。"

        def ensure_prompt(category_code: str, name: str, content: str) -> None:
            row = db.session.query(AiPromptTemplate).filter_by(category_code=category_code).first()
            if row is None:
                db.session.add(AiPromptTemplate(category_code=category_code, name=name, content=content, is_default=True))
                return
            # if content empty, fill (avoid overwriting user changes)
            if not row.content:
                row.content = content
                row.is_default = True
                return

            # Backfill outdated built-in defaults (avoid "hardcoded list" duplication).
            # Only update when it's still the default template (i.e. user likely didn't customize it).
            if row.is_default:
                s = str(row.content or "")
                # Old versions hardcoded categories/locations in the main prompt,
                # but runtime now injects dynamic option lists. Keep them consistent.
                if category_code == "text_extract":
                    if "必须从：调味品/食材" in s or "存放位置（string，例如：冰箱/冷藏" in s:
                        row.content = content
                        row.name = name
                if category_code == "vision_recognize":
                    if "尽量从：调味品/食材" in s or "存放位置（string，例如：冰箱/冷藏" in s:
                        row.content = content
                        row.name = name

        ensure_prompt("vision_recognize", "默认图像识别提示词", vision_default_prompt)
        ensure_prompt("text_extract", "默认文字识别提示词", text_default_prompt)
        ensure_prompt("interface_test", "默认接口测试提示词", interface_test_default_prompt)
        db.session.commit()

        # Seed default AI models for MVP connectivity testing.
        vision_request_template = json.dumps(
            {
                "model": "{{model}}",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_image", "image_url": "{{image_data_url}}"},
                            {"type": "input_text", "text": "{{prompt}}"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )

        text_request_template = json.dumps(
            {
                "model": "{{model}}",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "{{prompt}}"},
                            {"type": "input_text", "text": "用户输入：{{user_text}}"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )

        def ensure_model(
            *,
            name: str,
            display_name: str,
            api_type: str,
            base_url: str,
            model_name: str,
            request_template: str,
            response_parse_mode: str = "auto_json",
            capabilities: list[str] | None = None,
        ) -> None:
            row = db.session.query(AiModel).filter_by(name=name).first()
            if row is not None:
                # Backfill / refresh display_name for built-in rows (avoid duplicates & keep naming consistent)
                if row.name in {"默认-图像识别-Pro-Responses", "默认-文本解析-Pro-Responses"}:
                    row.display_name = display_name
                elif not (row.display_name or "").strip():
                    row.display_name = display_name
                # Backfill request_template for existing rows if missing/legacy
                if not (row.request_template or "").strip():
                    row.request_template = request_template
                # Backfill capabilities for existing rows if empty
                try:
                    cur = (row.capabilities_json or "[]").strip()
                    arr = json.loads(cur) if cur else []
                    if not isinstance(arr, list):
                        arr = []
                except Exception:
                    arr = []
                if (capabilities or []) and not arr:
                    row.capabilities_json = json.dumps(capabilities, ensure_ascii=False)
                # For GLM stream template: ensure it includes {{prompt}} / {{user_text}}
                if row.model_name == "glm-4-7-251222" and (
                    "{{prompt}}" not in (row.request_template or "") or "{{user_text}}" not in (row.request_template or "")
                ):
                    row.request_template = request_template
                # For DeepSeek stream template: ensure it includes {{prompt}} / {{user_text}}
                if row.model_name == "deepseek-v3-2-251201" and (
                    "{{prompt}}" not in (row.request_template or "") or "{{user_text}}" not in (row.request_template or "")
                ):
                    row.request_template = request_template
                # For Doubao 1.6 Lite chat vision template: ensure image/prompt placeholders exist
                if row.model_name == "doubao-seed-1-6-lite-251015" and (
                    "{{image_data_url}}" not in (row.request_template or "") or "{{prompt}}" not in (row.request_template or "")
                ):
                    row.request_template = request_template
                return
            db.session.add(
                AiModel(
                    name=name,
                    display_name=display_name,
                    api_type=api_type,
                    base_url=base_url,
                    model_name=model_name,
                    enabled=True,
                    request_template=request_template,
                    response_parse_mode=response_parse_mode,
                    response_success_contains="",
                    headers_extra_json="{}",
                    timeout_s=60,
                    capabilities_json=json.dumps(capabilities or [], ensure_ascii=False),
                )
            )

        ensure_model(
            name="默认-图像识别-Pro-Responses",
            display_name="豆包 Pro（图片识别）",
            api_type="responses",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="doubao-seed-2-0-pro-260215",
            request_template=vision_request_template,
            capabilities=["vision_recognize"],
        )
        ensure_model(
            name="默认-文本解析-Pro-Responses",
            display_name="豆包 Pro（文本解析）",
            api_type="responses",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="doubao-seed-2-0-pro-260215",
            request_template=text_request_template,
            capabilities=["text_extract"],
        )

        # Additional built-in engines for testing
        ensure_model(
            name="豆包-Lite-Responses",
            display_name="豆包-Lite（Responses）",
            api_type="responses",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="doubao-seed-2-0-lite-260215",
            request_template=text_request_template,
            capabilities=["text_extract", "recipes_generate"],
        )
        ensure_model(
            name="豆包-Mini-Responses",
            display_name="豆包-Mini（Responses）",
            api_type="responses",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="doubao-seed-2-0-mini-260215",
            request_template=text_request_template,
            capabilities=["text_extract", "recipes_generate"],
        )

        # Vision-capable variants (include image_data_url)
        ensure_model(
            name="豆包-Lite-图片识别-Responses",
            display_name="豆包-Lite（图片识别/Responses）",
            api_type="responses",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="doubao-seed-2-0-lite-260215",
            request_template=vision_request_template,
            capabilities=["vision_recognize"],
        )
        ensure_model(
            name="豆包-Mini-图片识别-Responses",
            display_name="豆包-Mini（图片识别/Responses）",
            api_type="responses",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="doubao-seed-2-0-mini-260215",
            request_template=vision_request_template,
            capabilities=["vision_recognize"],
        )

        # Chat Completions (vision-capable) example
        chat_vision_template = json.dumps(
            {
                "model": "{{model}}",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": "{{image_data_url}}"}},
                            {"type": "text", "text": "{{prompt}}"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )
        ensure_model(
            name="豆包-Code-Preview-ChatCompletions",
            display_name="豆包-Code Preview（Chat Completions）",
            api_type="chat_completions",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="doubao-seed-2-0-code-preview-260215",
            request_template=chat_vision_template,
            response_parse_mode="auto_json",
            capabilities=["vision_recognize", "recipes_generate"],
        )

        # Doubao Seed 2.0 Code Preview (chat/completions) - text+image blocks (same model, clearer purpose)
        ensure_model(
            name="豆包-Seed-2.0-CodePreview-图文对话",
            display_name="豆包 Seed 2.0 Code Preview（图文对话）",
            api_type="chat_completions",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="doubao-seed-2-0-code-preview-260215",
            request_template=chat_vision_template,
            response_parse_mode="auto_json",
            capabilities=["vision_recognize"],
        )

        # Code Preview text-extract dedicated template (adds user_text)
        code_preview_text_extract = json.dumps(
            {
                "model": "{{model}}",
                "messages": [
                    {
                        "role": "user",
                        "content": "{{prompt}}\n\n用户输入：{{user_text}}",
                    }
                ],
            },
            ensure_ascii=False,
        )
        ensure_model(
            name="豆包-Seed-2.0-CodePreview-文本解析",
            display_name="豆包 Seed 2.0 Code Preview（文本解析）",
            api_type="chat_completions",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="doubao-seed-2-0-code-preview-260215",
            request_template=code_preview_text_extract,
            response_parse_mode="auto_json",
            capabilities=["text_extract", "recipes_generate"],
        )

        # Image generation (seedream) - test raw response
        seedream_template = json.dumps(
            {
                "model": "{{model}}",
                "prompt": "{{prompt}}",
                "sequential_image_generation": "disabled",
                "response_format": "url",
                "size": "2K",
                "stream": False,
                "watermark": True,
            },
            ensure_ascii=False,
        )
        ensure_model(
            name="豆包-Seedream-图像生成",
            display_name="豆包-Seedream（Images Generations）",
            api_type="images_generations",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="doubao-seedream-5-0-260128",
            request_template=seedream_template,
            response_parse_mode="raw",
            capabilities=[],
        )

        # Video task (i2v) - test raw response
        video_task_template = json.dumps(
            {
                "model": "{{model}}",
                "content": [
                    {
                        "type": "text",
                        "text": "{{prompt}}",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "{{image_data_url}}"},
                    },
                ],
            },
            ensure_ascii=False,
        )
        ensure_model(
            name="豆包-Seedance-图生视频任务",
            display_name="豆包-Seedance（Video Task）",
            api_type="contents_generations_tasks",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="doubao-seedance-1-0-pro-250528",
            request_template=video_task_template,
            response_parse_mode="raw",
            capabilities=[],
        )

        # GLM example (Responses + stream + tools web_search)
        glm_stream_template = json.dumps(
            {
                "model": "{{model}}",
                "stream": True,
                "tools": [{"type": "web_search", "max_keyword": 3}],
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "{{prompt}}"},
                            {"type": "input_text", "text": "用户输入：{{user_text}}"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )
        ensure_model(
            name="GLM-4-7-联网搜索-Stream",
            display_name="GLM-4-7（Responses + WebSearch + Stream）",
            api_type="responses",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="glm-4-7-251222",
            request_template=glm_stream_template,
            response_parse_mode="raw",
            capabilities=["text_extract", "recipes_generate"],
        )

        # DeepSeek V3 example (Responses + stream + tools web_search)
        deepseek_stream_template = json.dumps(
            {
                "model": "{{model}}",
                "stream": True,
                "tools": [{"type": "web_search", "max_keyword": 3}],
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "{{prompt}}"},
                            {"type": "input_text", "text": "用户输入：{{user_text}}"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )
        ensure_model(
            name="DeepSeek-V3-联网搜索-Stream",
            display_name="DeepSeek-V3（Responses + WebSearch + Stream）",
            api_type="responses",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="deepseek-v3-2-251201",
            request_template=deepseek_stream_template,
            response_parse_mode="raw",
            capabilities=["text_extract", "recipes_generate"],
        )

        # Doubao Seed 1.6 Lite (Chat Completions vision + reasoning_effort)
        doubao_16_lite_chat_vision = json.dumps(
            {
                "model": "{{model}}",
                "max_completion_tokens": 65535,
                "reasoning_effort": "medium",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": "{{image_data_url}}"}},
                            {"type": "text", "text": "{{prompt}}"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )
        ensure_model(
            name="豆包-Seed-1.6-Lite-ChatCompletions",
            display_name="豆包-Seed-1.6 Lite（Chat Completions + Vision）",
            api_type="chat_completions",
            base_url="https://ark.cn-beijing.volces.com",
            model_name="doubao-seed-1-6-lite-251015",
            request_template=doubao_16_lite_chat_vision,
            response_parse_mode="auto_json",
            capabilities=["vision_recognize"],
        )
        db.session.commit()

    @app.after_request
    def _disable_cache(response):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    app.register_blueprint(main_bp)
    app.register_blueprint(items_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(recipes_bp)
    app.register_blueprint(ai_models_bp)
    app.register_blueprint(ai_prompts_bp)

    return app

