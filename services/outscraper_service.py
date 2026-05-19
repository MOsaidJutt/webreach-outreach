import re
import time
import logging
import requests
from flask import current_app

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5      # seconds between status checks
MAX_WAIT      = 300    # max 5 minutes total wait


class OutscraperService:
    """
    Fetches ALL results from Outscraper Google Maps (async API).
    No filtering applied — filtering happens in the UI / leads page.
    """

    BASE_URL  = "https://api.app.outscraper.com"
    CLOUD_URL = "https://api.outscraper.cloud"

    def __init__(self):
        self.api_key = current_app.config.get("OUTSCRAPER_API_KEY", "")
        if not self.api_key:
            raise ValueError("OUTSCRAPER_API_KEY is not configured.")
        self.headers = {"X-API-KEY": self.api_key}

    def search_all(self, query: str, location: str, limit: int = 200) -> list[dict]:
        """
        Search Google Maps and return ALL results.
        Handles Outscraper's async job flow automatically.
        """
        search_query = f"{query} in {location}" if location else query
        logger.info(f"Outscraper: starting async search for '{search_query}', limit={limit}")

        # Step 1 — kick off the async job
        resp = requests.get(
            f"{self.BASE_URL}/maps/search-v3",
            headers=self.headers,
            params={
                "query":          search_query,
                "limit":          min(limit, 500),
                "dropDuplicates": True,
                "fields": "name,rating,reviews,phone,type,full_address,city,state,country_code,place_id,site,url",
            },
            timeout=60,
        )
        resp.raise_for_status()
        job = resp.json()

        status      = job.get("status", "")
        poll_url    = job.get("results_location", "")
        job_id      = job.get("id", "")

        logger.info(f"Outscraper job {job_id} — initial status: {status}")

        # Step 2 — if already done (rare), return immediately
        if status == "Success":
            return self._extract(job.get("data", []))

        if not poll_url:
            raise RuntimeError(f"Outscraper returned no poll URL. Response: {job}")

        # Step 3 — poll until Success or timeout
        waited = 0
        while waited < MAX_WAIT:
            time.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL

            result = requests.get(poll_url, headers=self.headers, timeout=30)
            result.raise_for_status()
            data = result.json()
            current_status = data.get("status", "")

            logger.info(f"Outscraper job {job_id} — {waited}s — status: {current_status}")

            if current_status == "Success":
                results = self._extract(data.get("data", []))
                logger.info(f"Outscraper returned {len(results)} results for '{search_query}'")
                return results

            if current_status in ("Failed", "Error"):
                raise RuntimeError(f"Outscraper job failed: {data.get('errorMessage', 'Unknown error')}")

        raise RuntimeError(f"Outscraper job timed out after {MAX_WAIT}s")

    def _extract(self, raw_data) -> list[dict]:
        """Flatten nested list structure and normalise fields."""
        if not raw_data:
            return []
        # Outscraper sometimes returns [[...]] — flatten it
        if isinstance(raw_data[0], list):
            raw_data = [item for sub in raw_data for item in sub]
        return [self._normalize(biz) for biz in raw_data if isinstance(biz, dict)]

    def _normalize(self, raw: dict) -> dict:
        return {
            "business_name": raw.get("name", ""),
            "rating":        float(raw.get("rating") or 0),
            "reviews_count": int(raw.get("reviews") or 0),
            "phone":         self._clean_phone(raw.get("phone", "")),
            "category":      raw.get("type", ""),
            "address":       raw.get("full_address", ""),
            "city":          raw.get("city", ""),
            "state":         raw.get("state", ""),
            "country":       raw.get("country_code", ""),
            "google_maps_url": raw.get("url", ""),
            "place_id":      raw.get("place_id", ""),
            "website":       raw.get("site", "") or "",
        }

    def _clean_phone(self, phone: str) -> str:
        if not phone:
            return ""
        return re.sub(r"[^\d+]", "", str(phone))
