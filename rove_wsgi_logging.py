"""Keep standard journald logging without request secrets or exception payloads."""

import logging
from pathlib import Path
import traceback


class RuntimeLogFilter(logging.Filter):
    def filter(self, record):
        if record.exc_info:
            exception_type, _, exception_traceback = record.exc_info
            frames = [
                f'  File "{Path(frame.f_code.co_filename).name}", line {line}, in {frame.f_code.co_name}'
                for frame, line in traceback.walk_tb(exception_traceback)
            ]
            record.msg = "Runtime exception (%s)"
            record.args = (exception_type.__name__,)
            record.exc_info = None
            # Keep locations, never source lines, locals or exception payloads.
            record.exc_text = "\n".join([
                "Traceback (most recent call last):", *frames,
                f"{exception_type.__name__}: [details redacted]",
            ])
            record.stack_info = None
        elif record.name == "gunicorn.error" and str(record.msg).startswith("Invalid request"):
            record.msg = "Invalid HTTP request rejected"
            record.args = ()
        elif record.name == "gunicorn.error" and str(record.msg).startswith("Exception during worker exit"):
            record.msg = "Worker exit cleanup failed"
            record.args = ()
        return True


def configure_runtime_logging():
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(process)d] [%(levelname)s] %(name)s: %(message)s"
        ))
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    for handler in root.handlers:
        if not any(isinstance(item, RuntimeLogFilter) for item in handler.filters):
            handler.addFilter(RuntimeLogFilter())
    for name in ("gunicorn.error", "rove_app_api", "rove-app-api"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, RuntimeLogFilter) for item in logger.filters):
            logger.addFilter(RuntimeLogFilter())
