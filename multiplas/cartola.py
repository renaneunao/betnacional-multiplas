"""
Integração com API do Cartola como referência oficial das rodadas.
"""
import logging
from datetime import datetime
from typing import List, Dict, Optional
import requests

logger = logging.getLogger("multiplas.cartola")

CARTOLA_API = "https://api.cartola.globo.com/partidas"

TEAM_NAME_MAP: Dict[str, str] = {
    "Athlético-PR": "Athletico PR",
    "Atlético-MG": "Atlético MG",
    "Vasco": "Vasco da Gama",
    "Bragantino": "Red Bull Bragantino",
}


def cartola_to_betnacional_name(cartola_name: str) -> str:
    return TEAM_NAME_MAP.get(cartola_name, cartola_name)


class CartolaRound:
    def __init__(self, data: dict):
        self.rodada = data.get("rodada")
        self.clubes: Dict[int, str] = {}
        for club_id, club_data in data.get("clubes", {}).items():
            self.clubes[int(club_id)] = club_data.get("nome_fantasia", "")

        self.partidas: List[dict] = []
        for p in data.get("partidas", []):
            if p.get("valida", False):
                casa_id = p.get("clube_casa_id")
                visitante_id = p.get("clube_visitante_id")
                self.partidas.append({
                    "partida_id": p.get("partida_id"),
                    "data": p.get("partida_data"),
                    "casa": self.clubes.get(casa_id, f"ID:{casa_id}"),
                    "visitante": self.clubes.get(visitante_id, f"ID:{visitante_id}"),
                    "local": p.get("local"),
                })

    @classmethod
    def fetch(cls) -> "CartolaRound":
        logger.info("Fetching Cartola round data...")
        resp = requests.get(CARTOLA_API, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        round_num = data.get("rodada", "?")
        valid = sum(1 for p in data.get("partidas", []) if p.get("valida"))
        logger.info("Cartola: rodada %s, %d jogos válidos", round_num, valid)
        return cls(data)

    def get_match_by_teams(
        self, betnacional_matches: List[dict], casa_cartola: str, visitante_cartola: str
    ) -> Optional[dict]:
        casa_bn = cartola_to_betnacional_name(casa_cartola)
        visitante_bn = cartola_to_betnacional_name(visitante_cartola)

        for m in betnacional_matches:
            home = m.get("home_team", "")
            away = m.get("away_team", "")
            if home == casa_bn and away == visitante_bn:
                return m

        for m in betnacional_matches:
            home = m.get("home_team", "")
            away = m.get("away_team", "")
            if (casa_bn.lower() in home.lower() or home.lower() in casa_bn.lower()) and \
               (visitante_bn.lower() in away.lower() or away.lower() in visitante_bn.lower()):
                return m

        return None
