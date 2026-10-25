"""Register language learning on an existing Flask application."""
from pathlib import Path
from flask import Blueprint, Flask, current_app, g, request, url_for
from .storage import JsonContentStore, JsonProgressStore, StorageConflictError

PACKAGE_DIR = Path(__file__).resolve().parent


def init_learning(app):
    """Host integrations supply identity/storage/policies through app.config."""
    if "learning" in app.extensions:
        return app.extensions["learning"]
    app.config.setdefault("LEARNING_CONTENT_STORE", JsonContentStore(PACKAGE_DIR / "data"))
    app.config.setdefault("LEARNING_PROGRESS_STORE", JsonProgressStore(PACKAGE_DIR / "data/progress"))
    if "LEARNING_LEARNER_ID_RESOLVER" not in app.config:
        raise RuntimeError("Configure LEARNING_LEARNER_ID_RESOLVER from the authenticated account before registering learning")
    app.config.setdefault("LEARNING_ACCESS_CHECK", None)
    app.config.setdefault("LEARNING_MUTATION_CHECK", None)
    from .services import content, LearningServices
    from .routes import register_routes
    state = {"content_cache":{}, "languages":content.discover_languages(app.config["LEARNING_CONTENT_STORE"])}
    if not state["languages"]:
        raise RuntimeError("Learning content must define at least one language")
    app.extensions["learning"] = state
    blueprint = Blueprint("learning", __name__, template_folder="templates",
                          static_folder="static", static_url_path="/learning/static")

    @blueprint.before_request
    def check_learning_access():
        if request.endpoint == "learning.static":
            return None
        check = current_app.config["LEARNING_ACCESS_CHECK"]
        if check is not None:
            response = check()
            if response is not None:
                return response
        if request.method not in {"GET","HEAD","OPTIONS"}:
            mutation_check = current_app.config["LEARNING_MUTATION_CHECK"]
            if mutation_check is not None:
                return mutation_check()

    @blueprint.context_processor
    def learning_diagnostics():
        return {"learning_error_report_url":url_for("browser_error") if "browser_error" in current_app.view_functions else "",
                "learning_auth_script_url":current_app.config.get("LEARNING_AUTH_SCRIPT_URL")}

    @blueprint.errorhandler(StorageConflictError)
    def stale_progress(error):
        reference = getattr(g,"request_id",None)
        current_app.logger.warning("learning_progress_conflict")
        return {"ok":False,"error":"Progress changed in another request. Reload and try again.","request_id":reference},409

    include_home = not any(rule.rule == "/" and "GET" in rule.methods for rule in app.url_map.iter_rules())
    register_routes(blueprint, LearningServices(),include_home=include_home)
    app.register_blueprint(blueprint)
    return state


def create_app(config=None):
    """Standalone development factory; SaaS hosts call init_learning instead."""
    app = Flask(__name__, static_folder=None)
    app.config["DIAGNOSTICS_LOG_PATH"] = PACKAGE_DIR / "logs/app.jsonl"
    if config:
        app.config.update(config)
    from .diagnostics import init_diagnostics
    init_diagnostics(app)
    app.config.setdefault("LEARNING_LEARNER_ID_RESOLVER", lambda: "local-user")
    init_learning(app)
    return app
