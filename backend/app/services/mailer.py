"""Delivering a sign-in code, for free.

There is no paid email service here and there will not be one. Two ways a code
reaches a person, chosen by whether SMTP is configured:

**SMTP, when credentials exist.** Any ordinary mailbox works, including a Gmail
account with an app password. The credentials are read from the same
`/run/secrets` mount as every other secret, never baked into the image and
never passed as environment variables, because those end up in `docker inspect`
output and in `/proc/1/environ`.

**The server log, when they do not.** Loom runs on the machine of the person
signing in, so a code printed where they can see it is a legitimate delivery
channel for a single-user install rather than a degraded one. It is logged at
WARNING with a banner, because a code buried in INFO output during a busy
ingest is a code nobody finds.

The fallback is deliberately not silent about what it is. A person who has not
configured SMTP should understand immediately that their code is in the log and
not in their inbox, rather than sitting at an empty mailbox wondering.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    """Whether a server is available to send through.

    A password is not required: the bundled local catcher accepts any
    credentials, and demanding one here would make the default install fall
    back to the log despite a working mail server sitting next to it.
    """
    return bool(settings.smtp_host)


def _authenticate(server) -> None:
    """Log in only when there is something to log in with.

    An unauthenticated local relay rejects AUTH outright, so attempting it
    would fail the send on the one configuration that is meant to work with no
    setup at all.
    """
    if settings.smtp_username and settings.smtp_password:
        try:
            server.login(settings.smtp_username, settings.smtp_password)
        except smtplib.SMTPNotSupportedError:
            # The server does not offer AUTH. Fine for a local catcher, and a
            # real provider would have refused the connection already.
            pass


def _body(code: str, minutes: int) -> str:
    return (
        f"Your Loom sign-in code is {code}\n\n"
        f"It expires in {minutes} minutes and can be used once.\n\n"
        "If you did not ask to sign in, you can ignore this: the code is "
        "useless without access to this mailbox, and nothing has changed on "
        "your account.\n"
    )


def send_digest(email: str, subject: str, body: str) -> bool:
    """Send a digest. Returns False when it did not reach a mailbox.

    Unlike a sign-in code, a failed digest is not written to the log: it is not
    urgent, nobody is waiting on it, and dumping somebody's portfolio activity
    into a shared log to compensate would be worse than the missed email. The
    caller leaves the window open so the next run retries.
    """
    if not smtp_configured():
        logger.info("No SMTP configured; digest for %s not sent.", email)
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from or settings.smtp_username
    message["To"] = email
    message.set_content(body)
    return _deliver(message, email)


def send_login_code(email: str, code: str, *, minutes: int) -> bool:
    """Deliver a code. Returns True when it went to a mailbox.

    A False return is not an error: it means the code was written to the log
    for a local install, and the caller tells the user where to find it.
    """
    if not smtp_configured():
        logger.warning(
            "\n"
            "  ┌──────────────────────────────────────────────┐\n"
            "  │  LOOM SIGN-IN CODE                           │\n"
            "  │  %-44s│\n"
            "  │  %-44s│\n"
            "  │  Expires in %-2d minutes.                      │\n"
            "  └──────────────────────────────────────────────┘\n"
            "  No SMTP configured, so this was not emailed. Set SMTP_HOST,\n"
            "  SMTP_USERNAME and SMTP_PASSWORD to have codes delivered.",
            email, f"CODE: {code}", minutes,
        )
        return False

    message = EmailMessage()
    message["Subject"] = f"Your Loom sign-in code: {code}"
    message["From"] = settings.smtp_from or settings.smtp_username
    message["To"] = email
    message.set_content(_body(code, minutes))

    if _deliver(message, email):
        return True
    # Logged with the code, because a delivery failure must not leave somebody
    # locked out of their own local install. A digest gets no such treatment:
    # nobody is waiting on it, and dumping a portfolio into a shared log to
    # compensate would be worse than the missed mail.
    logger.warning("SMTP delivery failed; the code for %s is %s", email, code)
    return False


def _deliver(message: EmailMessage, email: str) -> bool:
    """One send path for every kind of mail Loom produces.

    Implicit TLS (465) and STARTTLS (587) are different things, and a container
    on the same Docker bridge supports neither and fails outright if asked to
    upgrade, which is why the choice is configuration rather than something
    always attempted.
    """
    try:
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, context=ssl.create_default_context()
            ) as server:
                _authenticate(server)
                server.send_message(message)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
                if settings.smtp_starttls:
                    server.starttls(context=ssl.create_default_context())
                _authenticate(server)
                server.send_message(message)
        logger.info("Mail sent to %s via %s.", email, settings.smtp_host)
        return True
    except Exception:
        logger.exception("SMTP delivery to %s failed.", email)
        return False


__all__ = ["send_digest", "send_login_code", "smtp_configured"]
