"""Neurosync AI Flask application."""

import json
import os
import secrets
from datetime import timedelta

from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from flask_login import LoginManager

import config
import database as db
from models import User
from routes.auth import auth_bp, session_record
from routes.dashboard import dash_bp


def create_app():
    static_dir = os.path.join(config.BASE_DIR, "static")
    app = Flask(__name__, template_folder=os.path.join(config.BASE_DIR, "templates"), static_folder=static_dir)
    app.config.update(
        SECRET_KEY=config.SECRET_KEY,
        SESSION_COOKIE_NAME="phantomlimb_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.PUBLIC_ORIGIN.startswith("https://"),
        SESSION_REFRESH_EACH_REQUEST=False,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SUPABASE_URL=config.SUPABASE_URL,
        SUPABASE_PUBLIC_KEY=config.SUPABASE_PUBLIC_KEY,
        SUPABASE_ADMIN_KEY=config.SUPABASE_ADMIN_KEY,
        PUBLIC_ORIGIN=config.PUBLIC_ORIGIN,
        DEFAULT_DEVICE=config.DEFAULT_DEVICE,
    )
    db.init_db()
    login_manager = LoginManager(app)

    @login_manager.request_loader
    def load_user(_request):
        record = session_record()
        user = db.get_user_by_id(record["user_id"]) if record and record["user_id"] else None
        return User(user, json.loads(record["assigned_patients"] or "null"), record["demo_patient_id"]) if user else None

    @login_manager.unauthorized_handler
    def unauthorized():
        unavailable = getattr(g, "auth_unavailable", False)
        if request.path.startswith("/api/"):
            return jsonify(status="unavailable" if unavailable else "unauthorized"), 503 if unavailable else 401
        if unavailable:
            return render_template("connection_error.html", message="Sign-in could not be refreshed. Try again shortly."), 503
        return redirect(url_for("auth.doctor_login" if request.path.startswith(("/expert", "/doctor")) else "auth.login", next=request.path))

    @app.before_request
    def protect_mutations():
        if request.method == "POST":
            expected = session.get("csrf_token")
            received = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            if not expected or not secrets.compare_digest(expected.encode(), received.encode()):
                abort(403)

    @app.context_processor
    def inject_csrf():
        def csrf_token():
            if "csrf_token" not in session:
                session["csrf_token"] = secrets.token_urlsafe(32)
            return session["csrf_token"]
        return {"csrf_token": csrf_token}

    @app.after_request
    def response_headers(response):
        if request.endpoint not in {"static", "manifest", "service_worker", "favicon", "apple_touch_icon", "offline"}:
            response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.register_blueprint(auth_bp)
    app.register_blueprint(dash_bp)

    @app.get("/")
    def root():
        return render_template("landing.html")

    @app.get("/manifest.json")
    def manifest():
        return send_from_directory(static_dir, "manifest.json", mimetype="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        response = send_from_directory(os.path.join(static_dir, "js"), "sw.js", mimetype="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.get("/offline")
    def offline():
        return render_template("offline.html")

    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-precomposed.png")
    def apple_touch_icon():
        return send_from_directory(os.path.join(static_dir, "icons"), "apple-touch-icon.png")

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(static_dir, "favicon.ico")

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=config.DEBUG, use_reloader=False)
