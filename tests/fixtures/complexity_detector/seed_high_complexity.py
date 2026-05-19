"""Fixture: a function with high cyclomatic AND high cognitive complexity.

SEED:high_complexity@classify_request — many decision points, nested branching.
"""


def classify_request(method, path, role, flags, headers):
    # SEED:high_complexity@classify_request
    result = "unknown"
    if method == "GET":
        if path.startswith("/admin"):
            if role == "admin":
                if flags.get("beta"):
                    result = "admin-beta-read"
                else:
                    result = "admin-read"
            elif role == "auditor":
                result = "audit-read"
            else:
                result = "denied"
        elif path.startswith("/api"):
            if "authorization" in headers:
                result = "api-read"
            else:
                result = "anon-read"
        else:
            result = "public-read"
    elif method == "POST":
        if role == "admin":
            if flags.get("dry_run"):
                result = "admin-dryrun"
            else:
                result = "admin-write"
        elif role == "writer":
            result = "writer-write"
        else:
            result = "denied"
    elif method == "DELETE":
        if role == "admin":
            result = "admin-delete"
        else:
            result = "denied"
    else:
        result = "method-not-allowed"
    return result
