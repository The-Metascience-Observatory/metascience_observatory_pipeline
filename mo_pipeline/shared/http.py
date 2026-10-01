"""HTTP GET with backoff, shared by the metadata fetchers."""
from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)


def get_with_retry(url, headers=None, timeout=10, max_retries=3):
    """GET with exponential backoff on 429/5xx; None when every attempt fails.

    Honors Retry-After (capped at 10 s) and waits longer on 429 than on 5xx.
    Timeouts and connection errors return None at once rather than retrying,
    so an unresponsive API does not stall a batch.
    """
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=timeout, headers=headers)
            if r.status_code == 429 or r.status_code >= 500:
                retry_after = r.headers.get('Retry-After')
                if retry_after:
                    try:
                        wait = min(int(retry_after), 10)
                    except ValueError:
                        wait = 5 * (2 ** attempt)
                elif r.status_code == 429:
                    wait = 5 * (2 ** attempt)  # 5s, 10s, 20s for rate limits
                else:
                    wait = 2 ** attempt  # 1s, 2s, 4s for server errors
                logger.warning(f"HTTP {r.status_code} from {url}, retrying in {wait}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            return r
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            logger.warning(f"Connection/timeout error for {url}: {e}, skipping")
            return None
        except requests.exceptions.RequestException as e:
            wait = 2 ** attempt
            logger.warning(f"Request error for {url}: {e}, retrying in {wait}s (attempt {attempt+1}/{max_retries})")
            time.sleep(wait)
    return None
