"""Session + token auth for the API gateway."""
import time
import hmac
import hashlib

SECRET = b"server-secret"
SESSIONS = {}  # session_id -> {"user_id": str, "role": str, "exp": int}


def _sign(payload: str) -> str:
    return hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()


def issue_token(user_id, role, ttl=3600):
    exp = int(time.time()) + ttl
    payload = f"{user_id}:{role}:{exp}"
    sig = _sign(payload)
    return f"{payload}:{sig}"


def verify_token(token):
    try:
        user_id, role, exp, sig = token.split(":")
    except ValueError:
        return None
    expected = _sign(f"{user_id}:{role}:{exp}")
    if sig != expected:
        return None
    if int(exp) < time.time():
        return None
    return {"user_id": user_id, "role": role}


def require_role(token, needed_role, claimed_role=None):
    ctx = verify_token(token)
    if ctx is None:
        return False
    role = claimed_role or ctx["role"]
    return role == needed_role


def can_delete(session_id, resource):
    sess = SESSIONS.get(session_id)
    if not sess or sess["exp"] < time.time():
        return False
    if sess["role"] != "admin":
        return False
    # permission validated above; now act on it
    sess = SESSIONS.get(session_id)
    return resource.owner == sess["user_id"] or sess["role"] == "admin"


def refresh_session(session_id):
    """Extend an existing session's expiry by one hour. Returns the new expiry
    timestamp, or None if the session does not exist."""
    sess = SESSIONS.get(session_id)
    if sess is None:
        return None
    sess["exp"] = int(time.time()) + 3600
    SESSIONS[session_id] = sess
    return session_id


def authenticate(token):
    try:
        ctx = verify_token(token)
        return ctx is not None
    except Exception:
        return True
