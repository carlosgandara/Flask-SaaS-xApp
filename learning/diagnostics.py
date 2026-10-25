"""Local request diagnostics with references shared by the browser and server log."""
import json
import logging
import os
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

import click
from flask import g, has_request_context, jsonify, render_template, request
from flask.logging import default_handler
from werkzeug.exceptions import HTTPException
from .storage.json_progress import ProgressStorageError
from .storage.contracts import StorageAccessError


class DiagnosticFormatter(logging.Formatter):
    def format(self, record):
        item = {"time":datetime.now(timezone.utc).isoformat(), "level":record.levelname,
                "pid":os.getpid(), "event":record.getMessage()}
        if has_request_context():
            item.update(request_id=getattr(g, "request_id", None), method=request.method,
                        route=str(request.url_rule) if request.url_rule else None,
                        endpoint=request.endpoint,
                        language=request.cookies.get("language") if request.cookies.get("language") in {"es","fr","tl"} else None)
        item.update(getattr(record, "diagnostic", {}))
        if record.exc_info:
            item["exception_type"] = record.exc_info[0].__name__
            item["traceback"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(item, ensure_ascii=False)


class SafeRotatingHandler(RotatingFileHandler):
    def handleError(self, record):
        # A broken log folder must still leave evidence in the terminal.
        try:
            sys.stderr.write("Diagnostic log unavailable; terminal fallback: " + self.format(record) + "\n")
        except Exception:
            sys.stderr.write("Diagnostic logging failed. Inspect the application terminal.\n")


def init_diagnostics(app):
    if "diagnostics" in app.extensions:
        return
    log_path = Path(app.config.get("DIAGNOSTICS_LOG_PATH", Path(app.root_path) / "logs" / "app.jsonl"))
    formatter = DiagnosticFormatter()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = SafeRotatingHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8", delay=True)
    except OSError:
        handler = logging.StreamHandler()
        sys.stderr.write("Cannot create diagnostic log directory; using terminal logging.\n")
    handler.setFormatter(formatter)
    app.logger.addHandler(handler)
    if default_handler in app.logger.handlers:
        app.logger.removeHandler(default_handler)
    if app.config.get("DIAGNOSTICS_CONSOLE", True):
        console = logging.StreamHandler()
        console.setLevel(logging.ERROR)
        console.setFormatter(formatter)
        app.logger.addHandler(console)
    app.logger.setLevel(logging.INFO)
    app.extensions["diagnostics"] = {"path":str(log_path), "handler":handler}

    @app.before_request
    def begin_request():
        g.request_id = uuid4().hex[:16]
        g.request_started = time.perf_counter()

    @app.after_request
    def finish_request(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", uuid4().hex[:16])
        elapsed = round((time.perf_counter() - getattr(g, "request_started", time.perf_counter())) * 1000, 1)
        level = logging.ERROR if response.status_code >= 500 else logging.WARNING if elapsed > 1000 else logging.INFO
        app.logger.log(level, "request_completed", extra={"diagnostic":{"status":response.status_code,"duration_ms":elapsed}})
        return response

    @app.errorhandler(Exception)
    def handle_exception(error):
        # Preserve deliberate 400/403/404/etc. rather than masking them as failures.
        if isinstance(error, HTTPException):
            return error
        reference = getattr(g, "request_id", uuid4().hex[:16])
        details = {"request_id":reference}
        storage = isinstance(error, StorageAccessError)
        if storage:
            details.update(operation=error.operation, storage_path=error.path,
                           errno=error.errno, winerror=error.winerror)
        app.logger.error("progress_storage_failed" if isinstance(error, ProgressStorageError) else "content_storage_failed" if storage else "unhandled_request_error",
                         exc_info=(type(error),error,error.__traceback__), extra={"diagnostic":details})
        status = 503 if storage else 500
        message = ("Learning data could not be read or saved. Please try again; if this continues, share the reference below."
                   if storage else "Something went wrong. Please share the reference below so the problem can be traced.")
        if request.is_json or request.method != "GET" or request.accept_mimetypes.best == "application/json":
            return jsonify(ok=False,error=f"{message} Reference: {reference}",request_id=reference),status
        try:
            return render_template("learning/error.html", message=message, reference=reference),status
        except Exception:
            app.logger.exception("error_page_render_failed",extra={"diagnostic":{"request_id":reference}})
            return f"Unable to continue. Reference: {reference}",status,{"Content-Type":"text/plain; charset=utf-8"}

    # Bounded, local-only browser reporting. Do not accept stack dumps, cookies,
    # page URLs, request bodies, or arbitrary client error messages into logs.
    browser_reports = deque()
    report_lock = threading.Lock()

    @app.post("/diagnostics/browser-error")
    def browser_error():
        if request.content_length is None or request.content_length > 2048:
            return "",413
        data = request.get_json(silent=True)
        if not isinstance(data,dict) or data.get("kind") not in {"script_error","unhandled_rejection"}:
            return "",400
        with report_lock:
            now = time.monotonic()
            while browser_reports and browser_reports[0] < now - 60:
                browser_reports.popleft()
            if len(browser_reports) >= 20:
                return "",429
            browser_reports.append(now)
        filename = data.get("file", "")
        filename = filename if isinstance(filename,str) and filename.startswith(("/static/", "/learning/static/")) and len(filename)<180 and "?" not in filename else None
        numeric = {key:data[key] for key in ("line","column") if type(data.get(key)) is int and 0 <= data[key] < 1_000_000}
        app.logger.error("browser_runtime_error",extra={"diagnostic":{"kind":data["kind"],"file":filename,**numeric}})
        return jsonify(ok=True,request_id=g.request_id)

    @app.cli.command("check-storage")
    def check_storage():
        """Check local progress access without replacing learner files."""
        store = app.config["LEARNING_PROGRESS_STORE"]
        if not hasattr(store,"check_access"):
            raise click.ClickException("The configured store does not support local file checks.")
        for language in ("tl","es","fr"):
            try:
                checked = store.check_access("local-user",language)
                click.echo(f"OK {language}: readable progress and writable directory ({checked})")
            except ProgressStorageError as error:
                app.logger.exception("storage_access_check_failed",extra={"diagnostic":{"operation":error.operation,"storage_path":error.path,"errno":error.errno,"winerror":error.winerror}})
                raise click.ClickException(f"{error}. See {log_path} for the cause.") from error

    @app.cli.command("show-error")
    @click.argument("reference")
    def show_error(reference):
        """Display the log events and readable traceback for a browser reference."""
        if len(reference) != 16 or any(c not in "0123456789abcdef" for c in reference):
            raise click.ClickException("Use the 16-character reference shown by the app.")
        found = False
        for candidate in [log_path, *[Path(str(log_path) + f".{i}") for i in range(1,4)]]:
            if not candidate.is_file():
                continue
            with candidate.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if item.get("request_id") != reference:
                        continue
                    found = True
                    stack = item.pop("traceback", None)
                    click.echo(json.dumps(item,indent=2,ensure_ascii=False))
                    if stack:
                        click.echo(stack)
        if not found:
            raise click.ClickException("No matching entry in the retained local logs.")


def install_process_hooks(app):
    def unhandled(exception_type,error,tb):
        if issubclass(exception_type,KeyboardInterrupt):
            return sys.__excepthook__(exception_type,error,tb)
        app.logger.critical("unhandled_process_error",exc_info=(exception_type,error,tb))
    sys.excepthook = unhandled

    def thread_error(args):
        app.logger.critical("unhandled_thread_error",exc_info=(args.exc_type,args.exc_value,args.exc_traceback),
                            extra={"diagnostic":{"thread":args.thread.name if args.thread else None}})
    threading.excepthook = thread_error
