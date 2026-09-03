# tria_engine/apps/billing/migrations/0002_seed_default_plan_tier.py
#
# Seed data, not schema: guarantees a single default "Free" tier exists on
# every fresh install so get_or_create_subscription() (billing/services.py)
# has a default to point new organizations at. Running on a database that
# already has billing PlanTier rows (e.g. an Admin created tiers first) is a
# no-op — the `exists()` guard keeps it idempotent.
#
# The free-tier-on-signup product decision this seed encodes is documented
# in billing/README.md: a price-0 default tier is activated with no payment
# on signup; if product later wants a paid-only funnel, the price on the
# default tier just needs to be > 0 and get_or_create_subscription() will
# leave new organizations in PENDING_PAYMENT instead.

from django.db import migrations


def seed_default_plan_tier(apps, schema_editor):
    PlanTier = apps.get_model("billing", "PlanTier")
    if PlanTier.objects.exists():
        return
    PlanTier.objects.create(
        name="Free",
        price="0.00",
        max_studies=3,
        max_users=10,
        storage_limit_gb=5,
        features=[
            "Up to 3 studies",
            "Up to 10 users",
            "5 GB storage",
        ],
        is_default=True,
        is_active=True,
    )


def unseed_default_plan_tier(apps, schema_editor):
    PlanTier = apps.get_model("billing", "PlanTier")
    PlanTier.objects.filter(name="Free", is_default=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_default_plan_tier, unseed_default_plan_tier),
    ]
