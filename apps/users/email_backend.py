from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
import resend


class ResendEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        resend.api_key = settings.RESEND_API_KEY
        sent_count = 0

        for message in email_messages:
            try:
                html_body = None

                if message.alternatives:
                    for alt_content, mimetype in message.alternatives:
                        if mimetype == "text/html":
                            html_body = alt_content
                            break

                resend.Emails.send({
                    "from": message.from_email or settings.DEFAULT_FROM_EMAIL,
                    "to": list(message.to),
                    "subject": message.subject,
                    "html": html_body or message.body.replace("\n", "<br>"),
                    "text": message.body,
                })
                sent_count += 1
            except Exception:
                if not self.fail_silently:
                    raise

        return sent_count
