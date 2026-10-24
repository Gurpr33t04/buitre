import socket

from buitre.config import TOR_CONTROL_HOST, TOR_CONTROL_PORT


def _send(sock: socket.socket, command: str) -> str:
    sock.sendall(command.encode("utf-8") + b"\r\n")

    return sock.recv(4096).decode("utf-8", "replace")


def new_identity(
    host: str = TOR_CONTROL_HOST,
    port: int = TOR_CONTROL_PORT,
    password: str | None = None,
    timeout: float = 5.0,
) -> bool:
    """
    Ask Tor for fresh circuits (SIGNAL NEWNYM) over the control port.

    Returns True on success, False if the control port is unavailable or
    authentication fails. Never raises: a failed rotation must not break
    a crawl. Tor itself rate-limits NEWNYM to about once per 10 seconds.

    Needs `ControlPort 9051` in torrc plus either a
    `HashedControlPassword` (pass `password`) or no authentication.
    """

    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            if password is not None:
                escaped = password.replace("\\", "\\\\").replace('"', '\\"')
                reply = _send(sock, f'AUTHENTICATE "{escaped}"')
            else:
                reply = _send(sock, "AUTHENTICATE")

            if not reply.startswith("250"):
                return False

            reply = _send(sock, "SIGNAL NEWNYM")
            ok = reply.startswith("250")

            try:
                _send(sock, "QUIT")
            except OSError:
                pass

            return ok

    except OSError:
        return False
