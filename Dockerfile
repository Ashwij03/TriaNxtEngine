# Backend image for TriaNxtEngine (Django).
#
# No Dockerfile existed in this repo before Task 5 / Part 2. This follows the
# non-root, hardened pattern already reviewed and approved in the CTMS
# Development Plan (Section 3), adapted to this repo's actual paths
# (split requirements/ files, tria_engine.wsgi, no top-level requirements.txt).

FROM python:3.11-slim

RUN groupadd -r django && useradd -r -g django django

WORKDIR /app

# Install dependencies first for better layer caching.
COPY tria_engine/requirements/ tria_engine/requirements/
RUN pip install --no-cache-dir -r tria_engine/requirements/prod.txt

COPY . .

RUN chown -R django:django /app
USER django

EXPOSE 8000

CMD ["gunicorn", "tria_engine.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-"]
