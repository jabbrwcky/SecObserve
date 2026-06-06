import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_session: requests.Session | None = None


def get_requests_session() -> requests.Session:
    global _session
    if _session is None:
        from django.conf import settings

        session = requests.Session()

        retries = getattr(settings, "HTTP_RETRIES", 0)
        if retries > 0:
            retry = Retry(
                total=retries,
                backoff_factor=0.5,
                status_forcelist=[500, 502, 503, 504],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry)
            session.mount("http://", adapter)
            session.mount("https://", adapter)

        if getattr(settings, "HTTP_DISABLE_KEEPALIVE", False):
            session.headers.update({"Connection": "close"})

        _session = session

    return _session
