"""Conservative SQLite runtime; systemd remains the only process supervisor."""

import os

bind = "127.0.0.1:" + str(int(os.getenv("ROVE_APP_API_PORT", "5057")))
# Auth/rate-limit buckets are process-local, so do not scale workers independently.
workers = 1
worker_class = "gthread"
threads = 4
worker_connections = 64
preload_app = False
max_requests = 0
timeout = 120
graceful_timeout = 90
keepalive = 5
worker_tmp_dir = "/run/rove-app-api"
forwarded_allow_ips = "127.0.0.1"
control_socket_disable = True
reload = False
daemon = False

accesslog = "-"
# No paths, query strings, headers, cookies, user IDs, bodies or client IPs.
access_log_format = "%(t)s pid=%(p)s method=%(m)s status=%(s)s duration=%(L)s"
errorlog = "-"
loglevel = "info"
capture_output = True


def on_starting(server):
    from rove_wsgi_logging import configure_runtime_logging

    configure_runtime_logging()
