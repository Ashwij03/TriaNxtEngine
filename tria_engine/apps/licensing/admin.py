from django.contrib import admin

from .models import (
    LicenseEntitlement,
    ReferralCode,
    ReferralProgramSettings,
    ReferralUsage,
)

admin.site.register(ReferralCode)
admin.site.register(ReferralUsage)
admin.site.register(LicenseEntitlement)
admin.site.register(ReferralProgramSettings)
