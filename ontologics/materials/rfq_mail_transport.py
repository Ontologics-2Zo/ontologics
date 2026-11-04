"""Single-attempt TLS SMTP submission; acceptance is not recipient delivery.

The caller preserves intent and MIME before entering this boundary. No retry,
provider response text, credentials, or mail contents leave it as error details.
"""
import smtplib
import socket
import ssl
import threading
import time

from ..service import DomainError

TIMEOUT = 8


def _configured(config):
    if not isinstance(config, dict):
        return False
    host, port = config.get('host'), config.get('port')
    username, password = config.get('username', ''), config.get('password', '')
    return (isinstance(host, str) and 0 < len(host) <= 253
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in host)
            and type(port) is int and 1 <= port <= 65535
            and config.get('tls') in ('ssl', 'starttls')
            and isinstance(username, str) and isinstance(password, str)
            and bool(username) == bool(password))


def submit(session, config, *, sender, recipient, raw):
    """Return a durable-outcome payload; never retry an ambiguous submission.

Common-budget failures before network propagate to the trusted caller. After
connection, failures before sendmail are definite non-submissions. Once the
SMTP transaction starts, only explicit rejection is safe to call rejected.
"""
    session._budget.consume('tool')
    session._budget.check()
    if not _configured(config):
        return dict(status='rejected', code='SMTP_CONFIG_INVALID')
    if recipient.rsplit('@', 1)[-1].lower().rstrip('.').endswith('.test'):
        return dict(status='rejected', code='SMTP_TEST_RECIPIENT')
    timeout = min(TIMEOUT, session._budget.remaining_seconds())
    deadline = time.monotonic() + timeout
    connection = None
    submitting = False
    expired = threading.Event()

    def close():
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def abort():
        expired.set()
        sock = getattr(connection, 'sock', None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        close()

    def before_step():
        session._budget.check()
        remaining = min(deadline-time.monotonic(), session._budget.remaining_seconds())
        if expired.is_set() or remaining <= 0:
            raise TimeoutError()
        sock = getattr(connection, 'sock', None)
        if sock is not None:
            sock.settimeout(remaining)

    timer = threading.Timer(timeout, abort)
    timer.daemon = True
    timer.start()
    try:
        context = ssl.create_default_context()
        # Construct without a hostname so the timer can see the connection
        # before connect/TLS starts. Per-socket timeouts also bound setup.
        if config['tls'] == 'ssl':
            connection = smtplib.SMTP_SSL(timeout=timeout, context=context)
        else:
            connection = smtplib.SMTP(timeout=timeout)
        # smtplib.connect() does not populate _host; both SSL and STARTTLS use
        # it for SNI/certificate hostname checks after deferred construction.
        connection._host = config['host']
        before_step()
        code, _ = connection.connect(config['host'], config['port'])
        if code != 220:
            return dict(status='rejected', code='SMTP_CONNECTION_REJECTED')
        before_step()
        code, _ = connection.ehlo()
        if not 200 <= code < 300:
            return dict(status='rejected', code='SMTP_CONNECTION_REJECTED')
        if config['tls'] == 'starttls':
            before_step()
            connection.starttls(context=context)
            before_step()
            code, _ = connection.ehlo()
            if not 200 <= code < 300:
                return dict(status='rejected', code='SMTP_CONNECTION_REJECTED')
        if config.get('username'):
            before_step()
            connection.login(config['username'], config['password'])
        before_step()
        submitting = True
        refused = connection.sendmail(sender, [recipient], raw)
        if refused:
            return dict(status='rejected', code='SMTP_REJECTED')
        # sendmail returned the server's acceptance. A simultaneous timeout or
        # failed connection close cannot turn known acceptance into unknown.
        return dict(status='smtp_accepted', accepted_at=session._service.now())
    except (smtplib.SMTPSenderRefused, smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError):
        return dict(status='rejected', code='SMTP_REJECTED')
    except DomainError:
        return dict(status='unknown' if submitting else 'rejected', code='SMTP_REQUEST_STOPPED')
    except (OSError, ValueError):
        return dict(status='unknown' if submitting else 'rejected',
                    code='SMTP_RESULT_UNKNOWN' if submitting else 'SMTP_CONNECTION_FAILED')
    finally:
        timer.cancel()
        # No QUIT round-trip is needed, and its failure must not hide a 250.
        close()
