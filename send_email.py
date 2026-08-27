import os
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")

try:
    from email_config import SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_AUTH_CODE, RECIPIENT_EMAIL
except ImportError:
    SMTP_SERVER = "smtp.qq.com"
    SMTP_PORT = 465
    SENDER_EMAIL = ""
    SENDER_AUTH_CODE = ""
    RECIPIENT_EMAIL = ""

# 环境变量覆盖（GitHub Actions 等 CI 环境）
if os.environ.get("SENDER_EMAIL"):
    SENDER_EMAIL = os.environ["SENDER_EMAIL"]
if os.environ.get("SENDER_AUTH_CODE"):
    SENDER_AUTH_CODE = os.environ["SENDER_AUTH_CODE"]
if os.environ.get("RECIPIENT_EMAIL"):
    RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]
if os.environ.get("SMTP_SERVER"):
    SMTP_SERVER = os.environ["SMTP_SERVER"]

SOURCE_TAG = os.environ.get("EMAIL_SOURCE_TAG") or ""


def send_report(html_path, subject=None, attach_html=True):
    if not os.path.exists(html_path):
        print(f"[邮件] HTML 文件不存在: {html_path}")
        return False
    if not SENDER_EMAIL or not SENDER_AUTH_CODE:
        print("[邮件] 未配置发件邮箱，跳过发送")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = SENDER_EMAIL
        msg["To"] = RECIPIENT_EMAIL
        date_str = datetime.now().strftime("%Y-%m-%d")
        if subject is None:
            subject = f"A股核心资产 KDJ 多周期信号报告 - {date_str}"
        msg["Subject"] = f"[{SOURCE_TAG}] {subject}" if SOURCE_TAG else subject

        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        part = MIMEText(html_content, "html", "utf-8")
        msg.attach(part)

        if attach_html:
            with open(html_path, "rb") as f:
                att = MIMEBase("application", "octet-stream")
                att.set_payload(f.read())
            encoders.encode_base64(att)
            att.add_header("Content-Disposition", "attachment", filename=os.path.basename(html_path))
            msg.attach(att)

        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SENDER_EMAIL, SENDER_AUTH_CODE)
            server.send_message(msg)

        print(f"[邮件] 报告已发送至 {RECIPIENT_EMAIL}")
        return True
    except Exception as e:
        print(f"[邮件] 发送失败: {e}")
        return False


def send_report_by_dir(out_dir):
    today = datetime.now().strftime("%Y-%m-%d")
    html_path = os.path.join(out_dir, f"report_{today}.html")
    if not os.path.exists(html_path):
        date_str = os.path.basename(out_dir)
        html_path = os.path.join(out_dir, f"report_{date_str}.html")
    return send_report(html_path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        send_report(sys.argv[1])
    else:
        print("用法: python send_email.py <html_file_path>")
