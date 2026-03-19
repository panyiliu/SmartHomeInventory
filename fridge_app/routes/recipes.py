from __future__ import annotations

import os
import time

from flask import Blueprint, jsonify, render_template, request

from ..services.recipes_service import generate_recipes
from ..services.recipes_service import get_available_ingredients


bp = Blueprint("recipes", __name__)


@bp.get("/recipes")
def index():
    return render_template("recipes.html")


@bp.post("/api/recipes/generate")
def api_generate():
    ingredients = get_available_ingredients()
    if not ingredients:
        return jsonify({"ok": True, "recipes": [], "reason": "no_ingredients"})

    payload = request.get_json(silent=True) or {}
    user_text = (payload.get("user_text") or "").strip()

    verbose = (os.environ.get("RECIPE_LOG_VERBOSE") or "1").strip() not in {"0", "false", "False", "OFF", "off"}
    start = time.time()
    if verbose:
        print("=" * 80)
        print("[RECIPES] generate start")
        print("ingredients_count:", len(ingredients))

    recipes, err = generate_recipes(user_text=user_text)
    if err:
        if verbose:
            cost_ms = int((time.time() - start) * 1000)
            print("[RECIPES] generate failed cost_ms:", cost_ms)
            print("error:", err)
            print("=" * 80)
        return jsonify({"ok": False, "error": err}), 400

    data = []
    for r in recipes:
        data.append(
            {
                "name": r.name,
                "ingredients": r.ingredients,
                "steps": [s.text for s in r.steps],
                "match_count": r.match_count,
                "total_count": r.total_count,
            }
        )

    if verbose:
        cost_ms = int((time.time() - start) * 1000)
        print("[RECIPES] generate ok cost_ms:", cost_ms)
        print("recipes_count:", len(data))
        print("=" * 80)
    return jsonify({"ok": True, "recipes": data})

