"""Public Despacio pages. Purchases continue through the existing billing routes."""
from flask import Blueprint, render_template, current_app, has_request_context
from auth.security import authenticated_user


def init_public_site(app):
    bp = Blueprint("site", __name__)

    @bp.get("/index.html")
    def index():
        return render_template("site/index.html")

    @bp.get("/about")
    def about():
        return render_template("site/about.html")

    @bp.get("/pricing")
    def pricing():
        return render_template("site/pricing.html", plans=current_app.config["BILLING_PLANS"])

    @bp.get("/games")
    def games():
        from learning.services.content import get_levels, language_name
        return render_template("site/games.html", game_levels=get_levels()['levels'], game_language=language_name())

    @app.context_processor
    def site_context():
        user = authenticated_user() if has_request_context() else None
        return {"site_user":user, "site_account":bool(user and user.verified)}

    app.register_blueprint(bp)
