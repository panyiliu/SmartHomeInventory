from __future__ import annotations

from flask import Blueprint, render_template


bp = Blueprint("recipes", __name__)


@bp.get("/recipes")
def index():
    return render_template("recipes.html")

