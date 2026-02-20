# Gunicorn configuration file
# Optimized for low-memory devices (e.g., Raspberry Pi with 512MB RAM)

# Server socket
bind = "0.0.0.0:8000"
backlog = 512

# Worker processes
# Reduced for low-memory devices (e.g., Raspberry Pi with 512MB RAM)
workers = 2
worker_class = "sync"
worker_connections = 500
timeout = 30
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
