# tria_engine/apps/licensing/tests.py
#
# Coverage for the same-organization + same-pincode referral guard (and the
# pincode field it depends on):
#   * same organization + same pincode ..................... blocked
#   * same organization + different known pincodes ......... allowed
#   * different organizations (any pincode combination) .... allowed
#   * same organization, pincode blank on either/both sides . blocked
#     (fail-closed — legacy rows are simulated with "" because that is the
#     exact placeholder the accounts 0002 migration wrote for accounts
#     created before the pincode field existed)
#   * registration requires a pincode (missing / blank / non-numeric are
#     all rejected by RegisterSerializer)
#   * the profile endpoint returns the user's pincode, allows updating it,
#     rejects clearing it or sending non-numeric values, and never lets a
#     user move their own organization/role.

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from tria_engine.apps.accounts.serializers import RegisterSerializer
from tria_engine.apps.organizations.models import Organization, Role

from . import services
from .models import ReferralCode

User = get_user_model()

PASSWORD = "password12345"


def _make_user(organization, *, username, pincode, role=None):
    return User.objects.create_user(
        username=username,
        email=f"{username}@test.test",
        password=PASSWORD,
        organization=organization,
        role=role or Role.objects.create(name="Admin", organization=organization),
        pincode=pincode,
    )


class SameOrganizationPincodeGuardTests(TestCase):
    """Business-rule matrix for redeem_referral_code()'s organization +
    pincode guard (fail-closed on unknown locations)."""

    def setUp(self):
        self.org_a = Organization.objects.create(name="Org A")
        self.org_b = Organization.objects.create(name="Org B")
        self.role_a = Role.objects.create(name="Admin", organization=self.org_a)
        self.role_b = Role.objects.create(name="Admin", organization=self.org_b)

    def _redeem(self, referrer, referee, code):
        ReferralCode.objects.create(user=referrer, code=code)
        return services.redeem_referral_code(referee, code)

    def test_redeem_blocked_same_org_same_pincode(self):
        referrer = _make_user(
            self.org_a, username="ref_r_same", pincode="560001", role=self.role_a
        )
        referee = _make_user(
            self.org_a, username="ref_e_same", pincode="560001", role=self.role_a
        )

        result = self._redeem(referrer, referee, code="ASH-AAA01")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "same_organization_referral")
        self.assertIn("same organization", result["message"].lower())

    def test_redeem_allowed_same_org_different_pincode(self):
        # Same organization but verifiably different sites/locations.
        referrer = _make_user(
            self.org_a, username="ref_r_diff", pincode="560001", role=self.role_a
        )
        referee = _make_user(
            self.org_a, username="ref_e_diff", pincode="600001", role=self.role_a
        )

        result = self._redeem(referrer, referee, code="ASH-BBB02")

        self.assertTrue(result["ok"])
        self.assertEqual(result["days_granted"], 15)

    def test_redeem_allowed_different_org_same_pincode(self):
        # Different organizations are always allowed, pincode irrelevant.
        referrer = _make_user(
            self.org_a, username="ref_r_dos", pincode="560001", role=self.role_a
        )
        referee = _make_user(
            self.org_b, username="ref_e_dos", pincode="560001", role=self.role_b
        )

        result = self._redeem(referrer, referee, code="ASH-CCC03")

        self.assertTrue(result["ok"])
        self.assertEqual(result["days_granted"], 15)

    def test_redeem_allowed_different_org_different_pincode(self):
        # Baseline happy path, unaffected by this change.
        referrer = _make_user(
            self.org_a, username="ref_r_dod", pincode="560001", role=self.role_a
        )
        referee = _make_user(
            self.org_b, username="ref_e_dod", pincode="700001", role=self.role_b
        )

        result = self._redeem(referrer, referee, code="ASH-DDD04")

        self.assertTrue(result["ok"])
        self.assertEqual(result["days_granted"], 15)

    def test_redeem_blocked_when_pincode_missing_on_both_sides(self):
        # Legacy rows: registration can no longer produce a blank pincode,
        # but the 0002 migration left pre-existing accounts with "".
        referrer = _make_user(
            self.org_a, username="ref_r_bb", pincode="", role=self.role_a
        )
        referee = _make_user(
            self.org_a, username="ref_e_bb", pincode="", role=self.role_a
        )

        result = self._redeem(referrer, referee, code="ASH-EEE05")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "same_organization_referral")

    def test_redeem_blocked_when_pincode_missing_on_one_side(self):
        # Referrer has a real pincode; referee's is blank (legacy row).
        referrer = _make_user(
            self.org_a, username="ref_r_known", pincode="560001", role=self.role_a
        )
        referee = _make_user(
            self.org_a, username="ref_e_unknown", pincode="", role=self.role_a
        )

        result = self._redeem(referrer, referee, code="ASH-FFF06")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "same_organization_referral")

        # And the mirror image: referrer blank, referee known.
        referrer2 = _make_user(
            self.org_a, username="ref_r_unknown", pincode="", role=self.role_a
        )
        referee2 = _make_user(
            self.org_a, username="ref_e_known", pincode="600001", role=self.role_a
        )

        result2 = self._redeem(referrer2, referee2, code="ASH-GGG07")

        self.assertFalse(result2["ok"])
        self.assertEqual(result2["reason"], "same_organization_referral")


class RegistrationPincodeTests(TestCase):
    """RegisterSerializer must require a non-blank, numeric pincode."""

    def setUp(self):
        self.org = Organization.objects.create(name="Reg Org")
        self.role = Role.objects.create(name="Staff", organization=self.org)

    def _payload(self, **overrides):
        payload = {
            "username": "newuser",
            "email": "newuser@test.test",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
            "first_name": "New",
            "last_name": "User",
            "organization": self.org.id,
            "role": self.role.id,
            "pincode": "560001",
        }
        payload.update(overrides)
        return payload

    def test_registration_rejects_payload_missing_pincode(self):
        payload = self._payload()
        payload.pop("pincode")

        serializer = RegisterSerializer(data=payload)

        self.assertFalse(serializer.is_valid())
        self.assertIn("pincode", serializer.errors)

    def test_registration_rejects_empty_string_pincode(self):
        serializer = RegisterSerializer(data=self._payload(pincode=""))

        self.assertFalse(serializer.is_valid())
        self.assertIn("pincode", serializer.errors)

    def test_registration_rejects_non_numeric_pincode(self):
        serializer = RegisterSerializer(data=self._payload(pincode="abcd12"))

        self.assertFalse(serializer.is_valid())
        self.assertIn("pincode", serializer.errors)

    def test_registration_accepts_valid_pincode(self):
        serializer = RegisterSerializer(data=self._payload())

        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["pincode"], "560001")


class UserProfileAPITests(TestCase):
    """GET/PATCH /api/accounts/profile/ — own profile incl. pincode."""

    def setUp(self):
        self.org = Organization.objects.create(name="Prof Org")
        self.role = Role.objects.create(name="Admin", organization=self.org)
        self.other_org = Organization.objects.create(name="Other Org")
        self.other_role = Role.objects.create(name="CRA", organization=self.other_org)
        self.user = _make_user(
            self.org, username="pro_user", pincode="560001", role=self.role
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_get_returns_users_own_pincode(self):
        response = self.client.get("/api/accounts/profile/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "pro_user")
        self.assertEqual(response.data["pincode"], "560001")
        self.assertEqual(response.data["organization_name"], "Prof Org")
        self.assertEqual(response.data["role_name"], "Admin")

    def test_patch_updates_pincode(self):
        response = self.client.patch(
            "/api/accounts/profile/", {"pincode": "600001"}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pincode, "600001")

    def test_patch_rejects_non_numeric_pincode(self):
        response = self.client.patch(
            "/api/accounts/profile/", {"pincode": "abc123"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("pincode", response.data)

    def test_patch_rejects_clearing_pincode_to_blank(self):
        response = self.client.patch(
            "/api/accounts/profile/", {"pincode": ""}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("pincode", response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pincode, "560001")

    def test_patch_cannot_change_organization_or_role(self):
        response = self.client.patch(
            "/api/accounts/profile/",
            {
                "organization": self.other_org.id,
                "role": self.other_role.id,
                "pincode": "560002",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.organization_id, self.org.id)
        self.assertEqual(self.user.role_id, self.role.id)
        self.assertEqual(self.user.pincode, "560002")
