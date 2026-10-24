# Real backend image (Phase 13) -- supersedes the earlier placeholder
# noted in infra/docker-compose.yml ("no Dockerfile exists yet... later
# phases can add a real CMD/Dockerfile without changing this file's
# shape"). Build context must be the REPO ROOT, not backend/, since
# app/core/config.py resolves config/params.yaml three parents above
# itself -- see that module's docstring:
#
#   docker build -f backend/Dockerfile -t aicodequest-backend .
#
# gunicorn (not `flask run`/the Werkzeug dev server) -- config/params.yaml
# already documents "Do not use this outside local dev" for the dev
# server; this is that "outside local dev" path.
FROM python:3.13-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt gunicorn==23.0.0

COPY config/ config/
COPY backend/ backend/

WORKDIR /app/backend

EXPOSE 5055

# --factory: app.create_app is a Flask application FACTORY (accepts
# optional mongo_db/runner injection points for tests), not a module-level
# `app` instance -- gunicorn must be told to call it, not import it as a
# WSGI callable directly.
CMD ["gunicorn", "--bind", "0.0.0.0:5055", "--workers", "4", "--timeout", "120", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "--factory", "app:create_app"]
