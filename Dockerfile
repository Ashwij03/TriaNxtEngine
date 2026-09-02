# Build context: TriaNxtEngine/ (the folder containing manage.py)
FROM python:3.11-slim

RUN groupadd -r django && useradd -r -g django django
RUN mkdir -p /home/django && chown django:django /home/django

WORKDIR /app

# Install deps first for better layer caching
COPY tria_engine/requirements/ tria_engine/requirements/
RUN pip install --no-cache-dir -r tria_engine/requirements/prod.txt

COPY . .

RUN chown -R django:django /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=tria_engine.settings

USER django

EXPOSE 8000

CMD ["gunicorn", "tria_engine.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]