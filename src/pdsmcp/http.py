"""HTTP utilities — request helpers with retry logic."""

import logging
import time as _time

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10  # seconds
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 1  # seconds (doubles each retry)


def request_with_retry(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    **kwargs,
) -> requests.Response:
    """GET request with retry on timeout/connection errors.

    Args:
        url: URL to fetch.
        timeout: Per-request timeout in seconds.
        retries: Max number of attempts.
        backoff: Initial backoff in seconds (doubles each retry).
        **kwargs: Passed to requests.get().

    Returns:
        Response object.

    Raises:
        Last exception if all retries fail.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            last_exc = e
            if attempt < retries:
                wait = backoff * (2 ** (attempt - 1))
                logger.debug("Retry %d/%d for %s (wait %.1fs): %s",
                             attempt, retries, url, wait, e)
                _time.sleep(wait)
    raise last_exc
