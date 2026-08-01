import os

# Gunicorn configuration file
# Optimized for low-memory devices (e.g., Raspberry Pi with 512MB RAM)

# Server socket
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:80")
backlog = 512

# Worker processes
# Reduced for low-memory devices (e.g., Raspberry Pi with 512MB RAM)
workers = int(os.getenv("GUNICORN_WORKERS", "1"))
worker_class = "sync"
worker_connections = 500
timeout = int(os.getenv("GUNICORN_TIMEOUT", "30"))
keepalive = 2

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Process naming
proc_name = "portfolio_gunicorn"

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Control socket
# ~/.gunicorn is read-only under the systemd sandbox (ProtectHome=read-only),
# which caused "[Errno 30] Read-only file system" for gunicorn.ctl. Keep the
# control socket in a location that is writable for the service.
control_socket = "/var/lib/portfolio/gunicorn.ctl"

# SSL (if needed)
# keyfile = None
# certfile = None
