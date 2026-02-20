# Docker Deployment Guide

This guide explains how to run the Portfolio project using Docker with nginx and gunicorn.

## Prerequisites

- Docker installed ([Get Docker](https://docs.docker.com/get-docker/))
- Docker Compose installed ([Install Docker Compose](https://docs.docker.com/compose/install/))

## Project Structure

```
Portfolio/
├── Dockerfile                 # Django app container configuration
├── docker-compose.yml         # Multi-container orchestration
├── entrypoint.sh             # Startup script for Django container
├── gunicorn_config.py        # Gunicorn settings
├── .env                      # Environment variables (not in git)
├── .env.example              # Template for environment variables
└── nginx/
    └── nginx.conf            # Nginx reverse proxy configuration
```

## Quick Start

### 1. Configure Environment Variables

Copy the example environment file and update it with your settings:

```bash
cp .env.example .env
```

Edit `.env` and update the following:
- `SECRET_KEY`: Generate a new Django secret key
- `DEBUG`: Set to `False` for production
- `ALLOWED_HOSTS`: Add your domain names
- `DB_PASSWORD`: Change to a secure password
- Email settings for contact form functionality

### 2. Build and Start Containers

```bash
# Build and start all services
docker-compose up -d --build

# Check if containers are running
docker-compose ps
```

### 3. Create a Superuser (Optional)

To access the Django admin panel:

```bash
docker-compose exec web python manage.py createsuperuser
```

### 4. Access the Application

- **Application**: http://localhost
- **Admin Panel**: http://localhost/admin

## Docker Services

The application consists of three services:

### 1. **web** (Django + Gunicorn)
- Runs the Django application using Gunicorn WSGI server
- Handles application logic and serves dynamic content
- Exposes port 8000 internally to nginx

### 2. **db** (PostgreSQL)
- Database service using PostgreSQL 15
- Persists data in a Docker volume
- Accessible on port 5432

### 3. **nginx**
- Reverse proxy server
- Serves static files and media
- Forwards dynamic requests to Gunicorn
- Listens on port 80

## Common Commands

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f web
docker-compose logs -f nginx
docker-compose logs -f db
```

### Stop Services

```bash
# Stop all containers
docker-compose stop

# Stop and remove containers
docker-compose down

# Stop and remove containers + volumes (WARNING: deletes database)
docker-compose down -v
```

### Restart Services

```bash
# Restart all services
docker-compose restart

# Restart specific service
docker-compose restart web
```

### Run Django Management Commands

```bash
# Run migrations
docker-compose exec web python manage.py migrate

# Create superuser
docker-compose exec web python manage.py createsuperuser

# Collect static files
docker-compose exec web python manage.py collectstatic --noinput

# Access Django shell
docker-compose exec web python manage.py shell
```

### Access Container Shell

```bash
# Access web container bash
docker-compose exec web bash

# Access database
docker-compose exec db psql -U portfolio_user -d portfolio_db
```

## Development vs Production

### Development Mode

For development, you can use SQLite instead of PostgreSQL:

1. Update `.env`:
```env
DEBUG=True
DB_ENGINE=django.db.backends.sqlite3
```

2. Comment out the `db` service in `docker-compose.yml`
3. Remove `depends_on: - db` from the web service

### Production Checklist

- [ ] Set `DEBUG=False` in `.env`
- [ ] Generate a new `SECRET_KEY`
- [ ] Update `ALLOWED_HOSTS` with your domain
- [ ] Change database password
- [ ] Configure email settings
- [ ] Set up HTTPS (add SSL certificates to nginx)
- [ ] Review and update `gunicorn_config.py` for your needs
- [ ] Set up regular database backups

## Volume Management

### Backup Database

```bash
# Create backup
docker-compose exec db pg_dump -U portfolio_user portfolio_db > backup.sql

# Restore backup
cat backup.sql | docker-compose exec -T db psql -U portfolio_user -d portfolio_db
```

### Named Volumes

The project uses Docker volumes for data persistence:
- `postgres_data`: PostgreSQL database
- `static_volume`: Collected static files
- `media_volume`: User-uploaded media files

## Troubleshooting

### Port Already in Use

If port 80 is already in use, change it in `docker-compose.yml`:
```yaml
nginx:
  ports:
    - "8080:80"  # Use port 8080 instead
```

### Database Connection Issues

Make sure PostgreSQL is ready before the web service starts. The `entrypoint.sh` script handles this, but if issues persist:

```bash
# Check database logs
docker-compose logs db

# Restart services
docker-compose restart
```

### Static Files Not Loading

```bash
# Rebuild and collect static files
docker-compose exec web python manage.py collectstatic --noinput
docker-compose restart nginx
```

### Permission Issues

```bash
# Fix permissions on uploaded media
docker-compose exec web chown -R www-data:www-data /app/media
```

## Updating the Application

```bash
# Pull latest changes
git pull

# Rebuild containers
docker-compose up -d --build

# Run migrations
docker-compose exec web python manage.py migrate

# Collect static files
docker-compose exec web python manage.py collectstatic --noinput
```

## Clean Up

Remove all containers, networks, and volumes:

```bash
docker-compose down -v
docker system prune -a
```

## Additional Resources

- [Django Documentation](https://docs.djangoproject.com/)
- [Gunicorn Documentation](https://docs.gunicorn.org/)
- [Nginx Documentation](https://nginx.org/en/docs/)
- [Docker Documentation](https://docs.docker.com/)
