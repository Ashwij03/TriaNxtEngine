# apps/accounts/tests/test_file_services.py

import shutil
import tempfile
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from tria_engine.apps.organizations.models import Organization
from tria_engine.apps.accounts.models import UploadedDocument

User = get_user_model()


TEMP_MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(
    MEDIA_ROOT=TEMP_MEDIA_ROOT,
    TRIA_UPLOADS={
        "DOCUMENT_MAX_SIZE": 1024 * 1024,
        "DOCUMENT_ALLOWED_EXTENSIONS": ["pdf", "docx", "txt"],
        "PROFILE_PHOTO_MAX_SIZE": 1024 * 1024,
        "PROFILE_PHOTO_ALLOWED_EXTENSIONS": ["jpg", "jpeg", "png"],
    }
)
class FileFeatureTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.client = APIClient()
        self.organization = Organization.objects.create(name="Test Org")
        self.user = User.objects.create_user(
            username="john",
            email="john@example.com",
            password="TestPass123",
            organization=self.organization,
        )
        self.client.force_authenticate(user=self.user)

    def test_document_upload_success(self):
        uploaded_file = SimpleUploadedFile(
            "sample.pdf",
            b"dummy pdf content",
            content_type="application/pdf",
        )

        response = self.client.post(
            "/api/accounts/documents/upload/",
            {"file": uploaded_file, "category": "general"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(UploadedDocument.objects.count(), 1)
        self.assertEqual(UploadedDocument.objects.first().original_name, "sample.pdf")

    def test_document_upload_invalid_extension(self):
        uploaded_file = SimpleUploadedFile(
            "sample.exe",
            b"dummy binary content",
            content_type="application/octet-stream",
        )

        response = self.client.post(
            "/api/accounts/documents/upload/",
            {"file": uploaded_file, "category": "general"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(UploadedDocument.objects.count(), 0)

    def test_document_download_success(self):
        uploaded_file = SimpleUploadedFile(
            "report.pdf",
            b"report content",
            content_type="application/pdf",
        )

        document = UploadedDocument.objects.create(
            uploaded_by=self.user,
            organization=self.organization,
            file=uploaded_file,
            original_name="report.pdf",
            content_type="application/pdf",
            file_size=len(b"report content"),
            category="general",
        )

        response = self.client.get(f"/api/accounts/documents/{document.id}/download/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get("Content-Disposition"), 'attachment; filename="report.pdf"')

    def test_profile_photo_upload_success(self):
        uploaded_file = SimpleUploadedFile(
            "avatar.png",
            (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
                b"\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01\xe5'"
                b"\xde\xfc\x00\x00\x00\x00IEND\xaeB`\x82"
            ),
            content_type="image/png",
        )

        response = self.client.post(
            "/api/accounts/profile-photo/upload/",
            {"file": uploaded_file},
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(bool(self.user.profile_photo))

    def test_profile_photo_upload_invalid_extension(self):
        uploaded_file = SimpleUploadedFile(
            "avatar.gif",
            b"gif-content",
            content_type="image/gif",
        )

        response = self.client.post(
            "/api/accounts/profile-photo/upload/",
            {"file": uploaded_file},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)