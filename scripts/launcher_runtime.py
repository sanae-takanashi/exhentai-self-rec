"""Allowlisted read-only startup identity; never infer it from the selected Python."""
from datetime import datetime
import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from scripts.launcher_core import NoRedirect


def validate_runtime(payload):
    if not isinstance(payload, dict) or payload.get("service") != "exh-rec" or type(payload.get("schema")) is not int or payload["schema"] != 1:
        raise ValueError()
    result = {}
    for key, pattern in (("revision", r"[0-9a-f]{40,64}"), ("source_digest", r"[0-9a-f]{64}"),
                         ("python", r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}")):
        value = payload[key]
        if value is None and key != "python":
            result[key] = None
        elif isinstance(value, str) and re.fullmatch(pattern, value):
            result[key] = value
        else:
            raise ValueError()
    value = payload["started_at"]
    if not isinstance(value, str) or len(value) > 40 or not datetime.fromisoformat(value).tzinfo:
        raise ValueError()
    result["started_at"] = value
    return result


def read_runtime_identity(home_url, timeout=0.6):
    try:
        endpoint = urllib.parse.urlsplit(home_url)
        ipaddress.IPv4Address(endpoint.hostname)
        if (endpoint.scheme != "http" or endpoint.username is not None or endpoint.password is not None
                or endpoint.path != "/" or endpoint.query or endpoint.fragment or not endpoint.port):
            raise ValueError()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(home_url + "api/runtime", timeout=timeout) as response:
            if not response.headers.get("Server", "").startswith("ExhRec/"):
                raise ValueError()
            raw = response.read(4097)
            if len(raw) > 4096:
                raise ValueError()
            identity = validate_runtime(json.loads(raw))
        return {"availability": "available", **identity}
    except urllib.error.HTTPError as error:
        unsupported = error.code == 404 and error.headers.get("Server", "").startswith("ExhRec/")
        error.close()
        return {"availability": "unsupported" if unsupported else "unavailable"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {"availability": "unavailable"}


def compare_source(local, runtime):
    if runtime.get("availability") != "available":
        return "unknown"
    current, started = local.get("source_digest"), runtime.get("source_digest")
    if not current or not started:
        return "unknown"
    return "matching" if current == started else "changed"
