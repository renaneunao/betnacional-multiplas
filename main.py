import json
import logging
import time
import random
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from multiplas.client import BetnacionalAPIClient
from multiplas.engine import MultiplesEngine
from multiplas.config import Config
from multiplas.models import ExecuteRequest, AnalysisResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("multiplas.api")

app = FastAPI(title="Betnacional Dashboard", version="2.0.0")

api_client = BetnacionalAPIClient()

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = static_dir / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>Dashboard not found</h1>"


@app.get("/analyze", response_model=AnalysisResult)
def analyze(
    num_legs: int = Query(default=None),
    stake: float = Query(default=None),
):
    try:
        n_legs = num_legs or Config.NUM_LEGS
        n_stake = stake or Config.STAKE
        matches = api_client.get_round_matches()
        engine = MultiplesEngine(num_legs=n_legs)
        result = engine.analyze(matches, stake=n_stake)
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
        target_ranks = set(payload.combinations) if payload.combinations else set(range(1, len(analysis.combinations) + 1))
        selected = [c for c in analysis.combinations if c.rank in target_ranks]
        results = []
        total = len(selected)
        base_delay = Config.BETWEEN_BETS_DELAY
        for i, combo in enumerate(selected):
            if i > 0:
                jitter = random.uniform(-0.3, 0.3) * base_delay
                delay = max(1.0, base_delay + jitter)
                time.sleep(delay)
            choices = [{"match_id": e["match_id"], "choice": e["choice"]} for e in combo.entries]
            try:
                resp = api_client.place_bet(choices, stake)
                results.append({"rank": combo.rank, "total_odd": combo.total_odd, "combined_prob": combo.combined_prob, "matches": combo.matches_summary, "result": resp})
            except Exception as e:
                results.append({"rank": combo.rank, "error": str(e)})
        balance = api_client.get_balance()
        return {"total_bets": total, "total_stake": total * stake, "balance_after": balance, "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute-stream")
def execute_stream(payload: ExecuteRequest):
    try:
        n_legs = payload.num_legs or Config.NUM_LEGS
        stake = payload.stake or Config.STAKE
        matches = api_client.get_round_matches()
        engine = MultiplesEngine(num_legs=n_legs)
        analysis = engine.analyze(matches, stake=stake)
        target_ranks = set(payload.combinations) if payload.combinations else set(range(1, len(analysis.combinations) + 1))
        selected = [c for c in analysis.combinations if c.rank in target_ranks]
        total = len(selected)
        base_delay = Config.BETWEEN_BETS_DELAY

        def generate():
            yield f"data: {json.dumps({'event': 'start', 'total': total, 'stake': stake, 'legs': n_legs})}\n\n"
            for i, combo in enumerate(selected):
                if i > 0:
                    jitter = random.uniform(-0.3, 0.3) * base_delay
                    delay = max(1.0, base_delay + jitter)
                    yield f"data: {json.dumps({'event': 'waiting', 'next': i+1, 'total': total, 'delay': round(delay, 1), 'rank': combo.rank})}\n\n"
                    time.sleep(delay)
                choices = [{"match_id": e["match_id"], "choice": e["choice"]} for e in combo.entries]
                try:
                    resp = api_client.place_bet(choices, stake)
                    entry = {"event": "bet", "n": i+1, "total": total, "rank": combo.rank, "total_odd": combo.total_odd, "combined_prob": combo.combined_prob, "matches": combo.matches_summary, "result": resp}
                except Exception as e:
                    entry = {"event": "bet", "n": i+1, "total": total, "rank": combo.rank, "matches": combo.matches_summary, "error": str(e)}
                yield f"data: {json.dumps(entry)}\n\n"
            try:
                balance = api_client.get_balance()
            except Exception:
                balance = None
            yield f"data: {json.dumps({'event': 'done', 'total_sent': total, 'balance': balance})}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/bets/pending")
def pending_bets(limit: int = 100):
    try:
        raw = api_client._get(f"/bets/history?status=pending&limit={limit}&date_start={(date.today() - timedelta(days=30)).isoformat()}&date_end={date.today().isoformat()}")
        bets = raw.get("bets", [])
        events = {e.get("id") or str(e.get("event_id")): e for e in raw.get("events", [])}

        grouped = {}
        for b in bets:
            tid = b.get("ticket_id", "?")
            if tid not in grouped:
                grouped[tid] = {
                    "ticket_id": tid,
                    "created_at": b.get("created_at"),
                    "status": b.get("bet_status_name"),
                    "stake": float(b.get("header_stake", 0)),
                    "total_odd": float(b.get("total_odd", 0)),
                    "potential_return": float(b.get("header_return", 0)),
                    "cashout_available": b.get("cashout_status") == 1,
                    "selections": [],
                }
            grouped[tid]["selections"].append({
                "home": b.get("home"),
                "away": b.get("away"),
                "outcome": b.get("outcome_name"),
                "odd": b.get("odd"),
                "current_odd": b.get("current_odd"),
                "event_date": b.get("event_date"),
                "sr_event_odd_id": b.get("sr_event_odd_id"),
                "tournament": b.get("tournament_name"),
            })

        return {
            "tickets": sorted(grouped.values(), key=lambda x: x.get("created_at", ""), reverse=True),
            "total_pending": sum(1 for t in grouped.values()),
            "total_stake": sum(t["stake"] for t in grouped.values()),
            "total_cashout_available": sum(1 for t in grouped.values() if t["cashout_available"]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/bets/settled")
def settled_bets(limit: int = 50):
    try:
        raw = api_client._get(f"/bets/history?status=completed&limit={limit}&date_start={(date.today() - timedelta(days=30)).isoformat()}&date_end={date.today().isoformat()}")
        bets = raw.get("bets", [])
        grouped = {}
        for b in bets:
            tid = b.get("ticket_id", "?")
            if tid not in grouped:
                grouped[tid] = {
                    "ticket_id": tid,
                    "created_at": b.get("created_at"),
                    "status": b.get("bet_status_name"),
                    "stake": float(b.get("header_stake", 0)),
                    "total_odd": float(b.get("total_odd", 0)),
                    "payout": float(b.get("header_return", 0)) if b.get("bet_status_name") == "Ganhou" else 0,
                    "selections": [],
                }
            grouped[tid]["selections"].append({
                "home": b.get("home"),
                "away": b.get("away"),
                "outcome": b.get("outcome_name"),
                "odd": b.get("odd"),
            })
        return {"tickets": sorted(grouped.values(), key=lambda x: x.get("created_at", ""), reverse=True)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ticket/{ticket_id}")
def ticket_detail(ticket_id: str):
    try:
        raw = api_client._get(f"/bets/details?ticket_id={ticket_id}")
        return raw
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cashout")
def cashout(ticket_id: str = Query(...), total_cashout: float = Query(0)):
    try:
        raw = api_client._post(f"/bets/cashout?ticket_id={ticket_id}&total_cashout={total_cashout}", data={})
        return raw
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status")
def status():
    try:
        balance = api_client.get_balance()
        return {"balance": balance, "config": {"num_legs": Config.NUM_LEGS, "stake": Config.STAKE, "between_bets_delay": Config.BETWEEN_BETS_DELAY}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/balance/stream")
def balance_stream():
    def generate():
        while True:
            try:
                balance = api_client.get_balance()
                yield f"data: {json.dumps({'balance': balance, 'time': time.strftime('%H:%M:%S')})}\n\n"
            except Exception:
                yield f"data: {json.dumps({'balance': None, 'error': True})}\n\n"
            time.sleep(15)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/health")
def health():
    return {"status": "healthy"}
