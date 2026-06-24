import hashlib
import re
from typing import Any, Optional

import requests
from django.conf import settings
from rest_framework.exceptions import ValidationError

try:
    from regopy import Input, Interpreter
    from regopy.rego_shared import RegoError

    REGOPY_AVAILABLE = True
except Exception:  # pragma: no cover - regopy is not available on ARM64
    Input = None
    Interpreter = None

    class RegoError(Exception):  # type: ignore[no-redef]
        """Fallback so the regopy backend's except clause has a valid type."""

    REGOPY_AVAILABLE = False


class RegoException(ValidationError):
    pass


class RegopyRegoInterpreter:
    """Rego interpreter backed by the embedded `regopy` library."""

    def __init__(self, rego_module: str) -> None:
        self.policy = rego_module
        try:
            rego = Interpreter()
            rego.log_level = 1
            rego.add_module("rule", rego_module)
            self.rego_bundle = rego.build("data")
        except RegoError as e:
            raise RegoException(f"Error while building rego bundle: {str(e)}") from e

    def query(self, data: Optional[Any] = None) -> dict:
        try:
            rego_run = Interpreter()
            rego_run.set_input(Input(data))
            output = rego_run.query_bundle(self.rego_bundle)

            node = output.results
            if not node:
                raise RegoException("Rego output has no results")
            if not node[0].expressions:
                raise RegoException("Rego results have no expressions")
            result = node[0].expressions[0].get("rule")
            if result is None:
                raise RegoException("Rego expressions have no 'rule' element")
            return result
        except RegoError as e:
            raise RegoException(f"Error while querying rego module: {str(e)}") from e


_PACKAGE_RE = re.compile(r"^[ \t]*package[ \t]+[^\n]+", re.MULTILINE)


def _opa_error(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"

    message = body.get("message", "")
    errors = body.get("errors")
    if errors:
        details = "; ".join(error.get("message", "") for error in errors if isinstance(error, dict))
        if details:
            return f"{message}: {details}" if message else details

    return message or f"HTTP {response.status_code}"


class OpaServerRegoInterpreter:
    """Rego interpreter backed by an external OPA server.

    The rego module is uploaded as an OPA policy on construction and evaluated
    via the OPA Data API on every query. Each module is rewritten to a unique
    package name (derived from its content hash) before upload, so multiple
    modules that all declare `package rule` stay isolated from each other on the
    shared OPA server.
    """

    def __init__(self, rego_module: str) -> None:
        self.policy = rego_module
        self.base_url = settings.OPA_SERVER_URL.rstrip("/")
        self.timeout = settings.OPA_SERVER_TIMEOUT

        digest = hashlib.sha256(rego_module.encode("utf-8")).hexdigest()[:16]
        self.package_name = f"secobserve_rule_{digest}"
        self.policy_id = self.package_name

        if not _PACKAGE_RE.search(rego_module):
            raise RegoException("Error while building rego bundle: rego module has no 'package' declaration")
        opa_module = _PACKAGE_RE.sub(f"package {self.package_name}", rego_module, count=1)

        try:
            response = requests.put(
                f"{self.base_url}/v1/policies/{self.policy_id}",
                data=opa_module.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise RegoException(f"Error while connecting to OPA server: {str(e)}") from e

        if response.status_code != 200:
            raise RegoException(f"Error while building rego bundle: {_opa_error(response)}")

    def query(self, data: Optional[Any] = None) -> dict:
        try:
            response = requests.post(
                f"{self.base_url}/v1/data/{self.package_name}",
                json={"input": data},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise RegoException(f"Error while querying rego module: {str(e)}") from e

        if response.status_code != 200:
            raise RegoException(f"Error while querying rego module: {_opa_error(response)}")

        try:
            body = response.json()
        except ValueError as e:
            raise RegoException(f"Error while querying rego module: invalid OPA response: {str(e)}") from e

        if "result" not in body:
            raise RegoException("Rego output has no results")

        result = body["result"]
        if result is None:
            raise RegoException("Rego output has no results")

        return result


def _use_opa_server() -> bool:
    return bool(getattr(settings, "OPA_USE_SERVER", False)) or not REGOPY_AVAILABLE


class RegoInterpreter:
    """Facade selecting the rego backend.

    Uses the embedded `regopy` interpreter by default, and the external OPA
    server when `OPA_USE_SERVER` is set or when `regopy` could not be imported
    (e.g. on ARM64).
    """

    def __init__(self, rego_module: str) -> None:
        self.policy = rego_module
        if _use_opa_server():
            self._backend: Any = OpaServerRegoInterpreter(rego_module)
        else:
            self._backend = RegopyRegoInterpreter(rego_module)

    def query(self, data: Optional[Any] = None) -> dict:
        return self._backend.query(data)
