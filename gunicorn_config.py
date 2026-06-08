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

# SSL (if needed)
# keyfile = None
# certfile = None
