import logging
from fastapi import FastAPI, HTTPException, Query

from multiplas.client import BetnacionalAPIClient
from multiplas.engine import MultiplesEngine
from multiplas.config import Config
from multiplas.models import ExecuteRequest, AnalysisResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("multiplas.api")

app = FastAPI(title="Betnacional Múltiplas Engine", version="1.0.0")

api_client = BetnacionalAPIClient()


@app.get("/")
def root():
    return {"status": "ok", "app": "betnacional-multiplas-engine"}


@app.get("/analyze", response_model=AnalysisResult)
def analyze(
    num_legs: int = Query(default=None, description="Jogos por múltipla"),
    stake: float = Query(default=None, description="Valor por entrada"),
):
    try:
        n_legs = num_legs or Config.NUM_LEGS
        n_stake = stake or Config.STAKE

        logger.info("Fetching round matches from Cartola + Betnacional...")
        matches = api_client.get_round_matches()
        logger.info("Got %d matches for the round", len(matches))

        engine = MultiplesEngine(num_legs=n_legs)
        result = engine.analyze(matches, stake=n_stake)

        logger.info(
            "Analysis complete: %d matches, %d legs, %d combos",
            result.num_matches, result.num_legs, result.total_combinations,
        )

        return result
    except Exception as e:
        logger.error("Error in /analyze: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute")
def execute(payload: ExecuteRequest):
    try:
        n_legs = payload.num_legs or Config.NUM_LEGS
        stake = payload.stake or Config.STAKE

        matches = api_client.get_round_matches()
        engine = MultiplesEngine(num_legs=n_legs)
        analysis = engine.analyze(matches, stake=stake)

        if payload.combinations:
            target_ranks = set(payload.combinations)
            selected = [c for c in analysis.combinations if c.rank in target_ranks]
        else:
            selected = analysis.combinations

        results = []
        for combo in selected:
            choices = [
                {"match_id": e["match_id"], "choice": e["choice"]}
                for e in combo.entries
            ]
            try:
                resp = api_client.place_bet(choices, stake)
                results.append({
                    "rank": combo.rank,
                    "matches": combo.matches_summary,
                    "total_odd": combo.total_odd,
                    "combined_prob": combo.combined_prob,
                    "result": resp,
                })
                logger.info("Bet #%d placed: %s", combo.rank, resp)
            except Exception as e:
                results.append({
                    "rank": combo.rank,
                    "matches": combo.matches_summary,
                    "error": str(e),
                })
                logger.error("Bet #%d failed: %s", combo.rank, e)

        balance = api_client.get_balance()

        return {
            "total_bets": len(selected),
            "total_stake": len(selected) * stake,
            "balance_after": balance,
            "results": results,
        }
    except Exception as e:
        logger.error("Error in /execute: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/status")
def status():
    try:
        balance = api_client.get_balance()
        return {
            "balance": balance,
            "config": {
                "num_legs": Config.NUM_LEGS,
                "stake": Config.STAKE,
                "api_url": Config.BETNACIONAL_API_URL,
            },
        }
    except Exception as e:
        logger.error("Error in /status: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "healthy"}
