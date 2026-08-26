import os

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
SENDER_AUTH_CODE = os.environ.get("SENDER_AUTH_CODE", "")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "")
