from django.apps import AppConfig


class HealthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tria_engine.apps.health"
    verbose_name = "Health Checks"
