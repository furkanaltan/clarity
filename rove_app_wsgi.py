"""Production WSGI entry point; no development server or background scheduler."""

try:
    import rove_app_api as api

    if not api.DB_PATH.is_file():
        raise FileNotFoundError("API database must already exist")
    api.prepare_runtime_schema()
except Exception as exc:
    # Gunicorn also prints startup exceptions directly to stderr.
    raise RuntimeError(f"API startup failed ({type(exc).__name__})") from None

app = api.app
