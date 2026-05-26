import logging
from typing import List, Dict, Any, Optional
import requests

from multiplas.config import Config
from multiplas.models import MatchOdds, Outcome
from multiplas.cartola import CartolaRound

logger = logging.getLogger("multiplas.client")


class BetnacionalAPIClient:
    def __init__(self, base_url: str = None):
        self.base_url = (base_url or Config.BETNACIONAL_API_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
        })

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        logger.debug("GET %s", url)
        try:
            resp = self.session.get(url, timeout=Config.TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error("API request failed: %s", e)
            raise

    def _post(self, path: str, data: dict) -> Any:
        url = f"{self.base_url}{path}"
        logger.debug("POST %s", url)
        try:
            resp = self.session.post(url, json=data, timeout=Config.TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error("API request failed: %s", e)
            raise

    def get_matches_raw(self) -> List[dict]:
        raw = self._get("/matches")
        return raw.get("matches", [])

    def parse_match(self, m: dict) -> Optional[MatchOdds]:
        outcomes = []
        for market in m.get("markets", []):
            if market.get("id") == "1":
                for o in market.get("outcomes", []):
                    odd = float(o["value"])
                    outcomes.append(Outcome(
                        id=o["id"],
                        name=o["name"],
                        odd=odd,
                        implied_prob=0.0
                    ))

        if not outcomes:
            return None

        margin = sum(1.0 / o.odd for o in outcomes) - 1.0

        for o in outcomes:
            o.implied_prob = round((1.0 / o.odd) / (1.0 + margin), 6)

        return MatchOdds(
            match_id=m.get("id"),
            home_team=m.get("home_team"),
            away_team=m.get("away_team"),
            start_time=m.get("start_time"),
            outcomes=outcomes,
            margin=round(margin, 4)
        )

    def get_matches(self) -> List[MatchOdds]:
        raw_matches = self.get_matches_raw()
        result = []
        for m in raw_matches:
            parsed = self.parse_match(m)
            if parsed:
                result.append(parsed)
        return result

    def get_round_matches(self) -> List[MatchOdds]:
        cartola = CartolaRound.fetch()
        raw_matches = self.get_matches_raw()

        result = []
        unmatched = []

        for p in cartola.partidas:
            matched = cartola.get_match_by_teams(raw_matches, p["casa"], p["visitante"])
            if matched:
                parsed = self.parse_match(matched)
                if parsed:
                    parsed.start_time = p["data"]
                    result.append(parsed)
                    logger.info("Matched: %s vs %s (%s)", parsed.home_team, parsed.away_team, p["data"])
            else:
                unmatched.append(f"{p['casa']} vs {p['visitante']} ({p['data']})")

        if unmatched:
            logger.warning("Unmatched Cartola games: %s", unmatched)

        if not result:
            logger.warning("No Cartola matches found. Falling back to all Betnacional matches.")
            return self.get_matches()

        logger.info("Round matches: %d/%d matched from Cartola", len(result), len(cartola.partidas))
        return result

    def get_balance(self) -> float:
        raw = self._get("/balance")
        return float(raw.get("balance", 0.0))

    def place_bet(self, choices: List[Dict[str, str]], stake: float) -> dict:
        payload = {
            "choices": choices,
            "stake": stake
        }
        return self._post("/bet", payload)
