import os
import uuid
from datetime import timedelta
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from tria_engine.apps.organizations.models import Organization, Role
from tria_engine.apps.accounts.utils import encrypt_value, decrypt_value


def user_profile_photo_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"profile_photos/user_{instance.id}/{uuid.uuid4().hex}{ext}"


def document_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"documents/{instance.category}/{timezone.now().strftime('%Y/%m/%d')}/{uuid.uuid4().hex}{ext}"


class User(AbstractUser):
    email = models.EmailField(unique=True)
    

    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )
    profile_photo = models.ImageField(
        upload_to=user_profile_photo_path,
        null=True,
        blank=True,
    )

    must_change_password = models.BooleanField(default=False)
    failed_login_attempts = models.IntegerField(default=5)
    session_token = models.UUIDField(default=uuid.uuid4, editable=False)
    last_activity = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.email or self.username


class UploadedDocument(models.Model):
    CATEGORY_CHOICES = [
        ("general", "General"),
        ("patient", "Patient"),
        ("user", "User"),
    ]

    document_number = models.PositiveIntegerField(unique=True, editable=False, null=True, blank=True)

    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="uploaded_documents",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="uploaded_documents",
        null=True,
        blank=True,
    )

    file = models.FileField(upload_to=document_upload_path)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=255, blank=True, null=True)
    file_size = models.PositiveBigIntegerField(default=0)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default="general")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["document_number", "id"]

    def save(self, *args, **kwargs):
        if not self.document_number:
            self.document_number = UploadedDocument.objects.count() + 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.file:
            self.file.delete(save=False)
        super().delete(*args, **kwargs)
        self.renumber_documents()

    @classmethod
    def renumber_documents(cls):
        documents = cls.objects.all().order_by("document_number", "id")
        for index, document in enumerate(documents, start=1):
            if document.document_number != index:
                cls.objects.filter(id=document.id).update(document_number=index)

    def __str__(self):
        return f"Document {self.document_number} - {self.original_name}"


class PasswordResetToken(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="password_reset_tokens",
    )
    token = models.UUIDField(unique=True, editable=False, null=True, blank=True)
    otp_code = models.CharField(max_length=6, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    is_used = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=10)
        super().save(*args, **kwargs)

    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"{self.user.email} - Reset OTP"


class LoginOTP(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="login_otps",
    )
    otp_code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    is_used = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=5)
        super().save(*args, **kwargs)

    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"{self.user.email} - OTP"


# ============================================
# MedicalRecord FIRST
# ============================================

class MedicalRecord(models.Model):
    patient = models.ForeignKey(
        'Patient', 
        on_delete=models.CASCADE, 
        related_name='medical_records'
    )
    diagnosis = models.TextField()
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']


# ============================================
# Patient SECOND
# ============================================


class Patient(models.Model):
    STATUS_CHOICES = [
        ("screening", "Screening"),
        ("enrolled", "Enrolled"),
        ("withdrawn", "Withdrawn"),
        ("completed", "Completed"),
    ]

    patient_id = models.CharField(max_length=20, unique=True, editable=False)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default="screening")
    site = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="patients")

    first_name_encrypted = models.CharField(max_length=500, blank=True, null=True)
    last_name_encrypted = models.CharField(max_length=500, blank=True, null=True)
    date_of_birth_encrypted = models.CharField(max_length=500, blank=True, null=True)
    email_encrypted = models.CharField(max_length=500, blank=True, null=True)
    phone_encrypted = models.CharField(max_length=500, blank=True, null=True)
    address_encrypted = models.TextField(blank=True, null=True)
    medical_record_number_encrypted = models.CharField(max_length=500, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # For new patients only
        if not self.id:
            # Get count of existing patients
            count = Patient.objects.count()
            # Set ID starting from 1
            self.id = count + 1
            
            # Generate patient_id (PAT-001, PAT-002, etc.)
            self.patient_id = f"PAT-{count + 1:03d}"
        
        # If patient_id not set, generate it
        if not self.patient_id:
            count = Patient.objects.count()
            self.patient_id = f"PAT-{count + 1:03d}"
            
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        
        # Delete related medical records first
        self.medical_records.all().delete()
        
        # Call the actual delete
        super().delete(*args, **kwargs)
        
        # Renumber all patients starting from 1
        self._renumber_patients()

    def _renumber_patients(self):
        """Renumber all patients starting from 1."""
        patients = Patient.objects.all().order_by('id')
        
        for index, patient in enumerate(patients, start=1):
            new_patient_id = f"PAT-{index:03d}"
            Patient.objects.filter(id=patient.id).update(
                id=index,
                patient_id=new_patient_id
            )

    def set_pii(self, first_name=None, last_name=None, dob=None, email=None, phone=None, address=None, mrn=None):
        if first_name is not None:
            self.first_name_encrypted = encrypt_value(first_name)
        if last_name is not None:
            self.last_name_encrypted = encrypt_value(last_name)
        if dob is not None:
            self.date_of_birth_encrypted = encrypt_value(dob)
        if email is not None:
            self.email_encrypted = encrypt_value(email)
        if phone is not None:
            self.phone_encrypted = encrypt_value(phone)
        if address is not None:
            self.address_encrypted = encrypt_value(address)
        if mrn is not None:
            self.medical_record_number_encrypted = encrypt_value(mrn)

    def get_pii(self):
        return {
            "first_name": decrypt_value(self.first_name_encrypted),
            "last_name": decrypt_value(self.last_name_encrypted),
            "date_of_birth": decrypt_value(self.date_of_birth_encrypted),
            "email": decrypt_value(self.email_encrypted),
            "phone": decrypt_value(self.phone_encrypted),
            "address": decrypt_value(self.address_encrypted),
            "medical_record_number": decrypt_value(self.medical_record_number_encrypted),
        }

    def __str__(self):
        return f"Patient {self.patient_id}"


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ("LOGIN", "LOGIN"),
        ("FAILED_LOGIN", "FAILED_LOGIN"),
        ("PASSWORD_CHANGE", "PASSWORD_CHANGE"),
        ("RESET_PASSWORD", "RESET_PASSWORD"),
        ("MFA_VERIFIED", "MFA_VERIFIED"),
        ("REGISTER", "REGISTER"),
        ("FORGOT_PASSWORD", "FORGOT_PASSWORD"),
        ("FAILED_PASSWORD_CHANGE", "FAILED_PASSWORD_CHANGE"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs"
    )

    action = models.CharField(
        max_length=100,
        choices=ACTION_CHOICES
    )

    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True
    )

    description = models.TextField()

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    signature_token = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True
    )

    signature_meaning = models.CharField(
        max_length=255,
        default=""
    )

    def __str__(self):
        return f"{self.user} - {self.action}"


class UploadForm(models.Model):

    FORM_TYPES = [
        ("IMAGE", "Image"),
        ("LAB_REPORT", "Lab Report"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="upload_forms"
    )

    file_name = models.CharField(
        max_length=255,
        blank=True,
        null=True
    )

    form_type = models.CharField(
        max_length=20,
        choices=FORM_TYPES
    )

    file = models.FileField(
        upload_to="forms/"
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def save(self, *args, **kwargs):

        if self.file:

            self.file_name = (
                self.file.name.split("/")[-1]
            )

        super().save(*args, **kwargs)

    def __str__(self):

        return (
            f"{self.form_type} - "
            f"{self.user.username}"
        )


class UploadLog(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="upload_logs"
    )
    
    file_name = models.CharField(
        max_length=255,
        blank=True,
        null=True
    )

    action = models.CharField(
        max_length=255
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return self.action