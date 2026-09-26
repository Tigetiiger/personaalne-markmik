import importlib
import os
import secrets
from datetime import timedelta

from flask import Flask

from . import auth, connections, db, images, jobs, security
from .ai import runner


def create_app(overrides=None):
    # /static is served by views.core.static_file (the bundle's caching and module re-exports).
    app = Flask(__name__, static_folder=None)
    app.config.update(
        DATABASE_PATH=os.environ.get("DATABASE_PATH", "data/markmik.db"),
        DATA_DIR=os.environ.get("DATA_DIR", "data"),
        OWNER_PASSWORD=os.environ.get("OWNER_PASSWORD"),
        OWNER_PASSWORD_HASH=os.environ.get("OWNER_PASSWORD_HASH"),
        # Random fallback: sessions then reset on restart. Production sets SECRET_KEY.
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        # Over Tailscale the app is plain HTTP; behind a tunnel HTTPS_ONLY=1.
        SESSION_COOKIE_SECURE=os.environ.get("HTTPS_ONLY") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    )
    if overrides:
        app.config.update(overrides)
    db.init_app(app)
    with app.app_context():
        conn = db.get_db()
        auth.bootstrap_owner(conn, app.config["OWNER_PASSWORD"], app.config["OWNER_PASSWORD_HASH"])
        images.seed_shared(conn)
    auth.init_app(app)
    security.init_app(app)
    connections.register("claude", "Claude", order=10, test=runner.test_connection, secret_names=("claude_token",))

    import_plugins()
    from .views import BLUEPRINTS
    for bp in BLUEPRINTS:
        app.register_blueprint(bp)
    return app


def import_plugins():
    """Import every module that registers jobs or connections, skipping ones not written yet."""
    for name in dict.fromkeys(jobs.JOB_MODULES + connections.CONNECTION_MODULES):
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
