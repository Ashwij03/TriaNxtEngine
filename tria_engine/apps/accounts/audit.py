#accounts/audit.py

import logging

audit_logger = logging.getLogger("audit")


def log_audit_event(event, user=None, patient=None, request=None, status="success", details=None):
    try:
        payload = {
            "event": event,
            "status": status,
            "user_id": getattr(user, "id", None),
            "user_email": getattr(user, "email", None),
            "patient_id": getattr(patient, "id", None),
            "patient_code": getattr(patient, "patient_id", None),
            "ip": request.META.get("REMOTE_ADDR") if request else None,
            "path": request.path if request else None,
            "method": request.method if request else None,
            "details": details or {},
        }
        audit_logger.info(payload)
    except Exception as e:
        audit_logger.error(f"Audit logging failed: {str(e)}")