"""Direct HTTPS requests to an explicitly configured internal service."""

import base64
import os
import shutil
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def approved_https(url, authority, label):
    """Require the exact configured hostname and optional port, without URL credentials."""
    parsed = urllib.parse.urlsplit(url)
    if (
        not authority or parsed.scheme != "https" or not parsed.hostname
        or parsed.netloc.lower() != authority.lower() or parsed.username is not None
        or parsed.password is not None or parsed.query or parsed.fragment
        or any(character.isspace() or character == "\\" for character in url)
    ):
        raise ValueError(f"{label} must use HTTPS at its configured internal host and port")
    if parsed.port == 0:
        raise ValueError(f"{label} has an invalid port")
    return parsed


def download_nexus(url, destination, credential_prefix):
    approved_https(url, os.environ.get("MYAPP_NEXUS_AUTHORITY", ""), "Nexus URL")
    user = os.environ.get(f"{credential_prefix}_USER")
    password = os.environ.get(f"{credential_prefix}_PASSWORD")
    if (user is None) != (password is None):
        raise ValueError(f"Set both {credential_prefix} credential variables or neither")
    request = urllib.request.Request(url)
    if user is not None:
        credential = base64.b64encode(f"{user}:{password}".encode()).decode()
        request.add_header("Authorization", f"Basic {credential}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=60) as response, destination.open("xb") as file:
        shutil.copyfileobj(response, file)
