from __future__ import annotations

import os
import time

from flask import Blueprint, current_app, jsonify, request

from ...services.recipes_service import generate_recipes, get_available_ingredients
from ...services.ai_job_service import ai_job_service


bp = Blueprint("recipes_api", __name__)


@bp.post("/api/recipes/generate")
def api_generate():
    ingredients = get_available_ingredients()
    if not ingredients:
        return jsonify({"ok": True, "recipes": [], "reason": "no_ingredients"})

    payload = request.get_json(silent=True) or {}
    user_text = (payload.get("user_text") or "").strip()

    async_flag_in_json = bool(payload.get("async")) if "async" in payload else False
    async_flag_qs = str(request.args.get("async") or "").strip().lower() in {"1", "true", "yes", "on"}
    async_flag = async_flag_in_json or async_flag_qs

    verbose = (os.environ.get("RECIPE_LOG_VERBOSE") or "1").strip() not in {"0", "false", "False", "OFF", "off"}
    start = time.time()
    if verbose:
        print("=" * 80)
        print("[RECIPES] generate start")
        print("ingredients_count:", len(ingredients))

    def _do_generate() -> list[dict]:
        recipes2, err = generate_recipes(user_text=user_text)
        if err:
            raise RuntimeError(err)
        data = []
        for r in recipes2:
            data.append(
                {
                    "name": r.name,
                    "ingredients": r.ingredients,
                    "steps": [s.text for s in r.steps],
                    "match_count": r.match_count,
                    "total_count": r.total_count,
                }
            )
        return data

    if async_flag:
        job_id = ai_job_service.create_job(
            kind="ai_recipes_generate",
            fn=_do_generate,
            meta={"user_text_len": len(user_text)},
            app=current_app._get_current_object(),
        )
        return jsonify({"ok": True, "async": True, "job_id": job_id})

    data = _do_generate()
    if verbose:
        cost_ms = int((time.time() - start) * 1000)
        print("[RECIPES] generate ok cost_ms:", cost_ms)
        print("recipes_count:", len(data))
        print("=" * 80)
    return jsonify({"ok": True, "recipes": data})

