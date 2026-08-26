import os

SMTP_SERVER = os.environ.get("SMTP_SERVER") or "smtp.qq.com"
SMTP_PORT = int(os.environ.get("SMTP_PORT") or 465)
SENDER_EMAIL = os.environ.get("SENDER_EMAIL") or ""
SENDER_AUTH_CODE = os.environ.get("SENDER_AUTH_CODE") or ""
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL") or ""
