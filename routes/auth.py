"""Supabase email/password authentication and server-side session handling."""

import base64
import hashlib
import secrets
import time
from urllib.parse import urlsplit

from flask import (
    Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for,
)
from flask_login import current_user, login_required

import database as db
from models import is_expert
from supabase_backend import SupabaseError, auth

auth_bp = Blueprint("auth", __name__)


def session_record():
    if "supabase_session" not in g:
        handle = session.get("auth_session")
        record = db.get_auth_session(handle)
        if record and record["expires_at"] <= time.time() + 20:
            try:
                record = db.refresh_auth_session(
                    handle, record["refresh_token"],
                    lambda token: auth("token", params={"grant_type": "refresh_token"},
                                       body={"refresh_token": token}),
                )
            except SupabaseError as error:
                if error.status in (400, 401, 403):
                    db.delete_auth_session(handle)
                    session.pop("auth_session", None)
                elif record["expires_at"] <= time.time():
                    g.auth_unavailable = True
                record = None if error.status in (400, 401, 403) or getattr(g, "auth_unavailable", False) else record
        g.supabase_session = record
    return g.supabase_session


def _clear_sessions():
    for key in ("auth_session", "recovery_session"):
        record = db.get_auth_session(session.get(key))
        db.delete_auth_session(session.get(key))
        if record:
            try:
                auth("logout", token=record["access_token"], params={"scope": "local"})
            except SupabaseError:
                pass
    session.clear()


def _remember(payload, *, recovery=False, persistent=False):
    user_id = None if recovery else db.link_supabase_user(payload["user"])
    _clear_sessions()
    handle = secrets.token_urlsafe(32)
    duration = 900 if recovery else 30 * 86400 if persistent else 86400
    db.save_auth_session(handle, user_id, payload, time.time() + duration)
    session["recovery_session" if recovery else "auth_session"] = handle
    session.permanent = persistent


def _email_flow(kind, body):
    origin = current_app.config["PUBLIC_ORIGIN"]
    if not origin:
        if request.host.split(":")[0] not in ("localhost", "127.0.0.1"):
            raise SupabaseError(503, "origin_not_configured")
        origin = request.host_url.rstrip("/")
    verifier = secrets.token_urlsafe(48)
    challenge = hashlib.sha256(verifier.encode()).digest()
    body.update(code_challenge=base64.urlsafe_b64encode(challenge).decode().rstrip("="),
                code_challenge_method="s256")
    payload = auth(kind, params={"redirect_to": origin + url_for("auth.callback", flow=kind)}, body=body)
    session[f"pkce_{kind}"] = verifier
    return payload


def _show_error(error, fallback):
    if error.code in ("not_configured", "origin_not_configured") or error.status >= 500:
        flash("Authentication is temporarily unavailable. Please try again.", "error")
    elif error.status == 429:
        flash("Too many requests. Wait a moment before trying again.", "error")
    elif error.code == "email_not_confirmed":
        flash("Confirm your email using the link Supabase sent you, then sign in.", "info")
    else:
        flash(fallback, "error")


def _destination():
    target = request.args.get("next", "")
    parsed = urlsplit(target) if target.startswith("/") else None
    if parsed and not parsed.netloc and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("dashboard.dashboard_page")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    return _login(doctor_portal=False)


@auth_bp.route("/doctor/login", methods=["GET", "POST"])
@auth_bp.route("/expert/login", methods=["GET", "POST"])
def doctor_login():
    return _login(doctor_portal=True)


def _login(doctor_portal):
    if current_user.is_authenticated:
        if doctor_portal and not current_user.is_doctor:
            return render_template("login.html", doctor_portal=True, switch_account=True, email=""), 403
        return redirect(url_for("dashboard.doctor_page" if current_user.is_doctor else "dashboard.dashboard_page"))
    email = request.form.get("email", "").strip().lower()
    if request.method == "POST":
        try:
            payload = auth("token", params={"grant_type": "password"},
                           body={"email": email, "password": request.form.get("password", "")})
            is_doctor = is_expert(payload["user"])
            if doctor_portal and not is_doctor:
                auth("logout", token=payload["access_token"], params={"scope": "local"})
                flash("This account does not have expert access.", "error")
                return render_template("login.html", doctor_portal=True, email=email), 403
            _remember(payload, persistent=request.form.get("remember") == "on")
            return redirect(url_for("dashboard.doctor_page") if is_doctor else _destination())
        except SupabaseError as error:
            _show_error(error, "Check your email and password and try again.")
        except ValueError as error:
            flash(str(error), "error")
    return render_template("login.html", doctor_portal=doctor_portal, email=email)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard_page"))
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    if request.method == "POST":
        password = request.form.get("password", "")
        if not 3 <= len(username) <= 60:
            flash("Enter a name between 3 and 60 characters.", "error")
        elif len(password) < 8 or password != request.form.get("confirm_password"):
            flash("Enter matching passwords with at least eight characters.", "error")
        else:
            try:
                payload = _email_flow("signup", {"email": email, "password": password,
                                                  "data": {"username": username}})
                if payload.get("access_token"):
                    _remember(payload)
                    return redirect(url_for("dashboard.dashboard_page"))
                flash("Check your email for a confirmation link. Open it in this browser.", "success")
                return redirect(url_for("auth.login"))
            except SupabaseError as error:
                _show_error(error, "Could not create your account. Check the details or try signing in.")
            except ValueError as error:
                flash(str(error), "error")
    return render_template("register.html", username=username, email=email)


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        try:
            _email_flow("recover", {"email": request.form.get("email", "").strip().lower()})
            flash("If your account exists, a reset link will arrive by email. Open it in this browser.", "success")
            return redirect(url_for("auth.login"))
        except SupabaseError as error:
            _show_error(error, "Unable to send a reset link. Check your email address and try again.")
    return render_template("forgot_password.html")


@auth_bp.get("/auth/callback/<flow>")
def callback(flow):
    if flow not in ("signup", "recover"):
        return redirect(url_for("auth.login"))
    verifier = session.pop(f"pkce_{flow}", None)
    code = request.args.get("code")
    if verifier and code:
        try:
            payload = auth("token", params={"grant_type": "pkce"},
                           body={"auth_code": code, "code_verifier": verifier})
            _remember(payload, recovery=flow == "recover")
            return redirect(url_for("auth.reset_password" if flow == "recover" else "dashboard.dashboard_page"))
        except SupabaseError as error:
            _show_error(error, "This link has expired. Request a new email and try again.")
        except ValueError as error:
            flash(str(error), "error")
    else:
        flash("This link has expired or was opened in a different browser.", "error")
    return redirect(url_for("auth.forgot_password" if flow == "recover" else "auth.login"))


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    record = db.get_auth_session(session.get("recovery_session"))
    if not record:
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < 8 or password != request.form.get("confirm_password"):
            flash("Enter matching passwords with at least eight characters.", "error")
        else:
            try:
                auth("user", method="PUT", token=record["access_token"], body={"password": password})
                _clear_sessions()
                flash("Your password has been updated. Sign in with your new password.", "success")
                return redirect(url_for("auth.login"))
            except SupabaseError as error:
                _show_error(error, "Could not update your password. Try another password or request a fresh link.")
    return render_template("reset_password.html")


@auth_bp.post("/logout")
@login_required
def logout():
    _clear_sessions()
    return redirect(url_for("root"))
