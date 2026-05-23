import json
import logging
import time
import random
import os
from datetime import date, timedelta, datetime, timezone, timedelta as td
from fastapi import FastAPI, HTTPException, Query, Request, Response, Depends
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pathlib import Path
import secrets

from multiplas.client import BetnacionalAPIClient
from multiplas.engine import MultiplesEngine
from multiplas.config import Config
from multiplas.models import ExecuteRequest, AnalysisResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("multiplas.api")

app = FastAPI(title="OddStack Dashboard", version="2.1.0")

api_client = BetnacionalAPIClient()
security = HTTPBasic()

DASH_USER = os.getenv("DASH_USER", "renaneunao")
DASH_PASS = os.getenv("DASH_PASS", "!Senhas123")
BRT = timezone(td(hours=-3))

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def to_brt(ts_str: str) -> str:
    if not ts_str:
        return ""
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"]:
            try:
                dt_utc = datetime.strptime(ts_str.replace("T", " ").split(".")[0].split("+")[0], "%Y-%m-%d %H:%M:%S")
                dt_brt = dt_utc.replace(tzinfo=timezone.utc).astimezone(BRT)
                return dt_brt.strftime("%d/%m/%Y %H:%M")
            except ValueError:
                continue
        return ts_str
    except Exception:
        return ts_str


def check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    is_ok = secrets.compare_digest(credentials.username, DASH_USER) and secrets.compare_digest(credentials.password, DASH_PASS)
    if not is_ok:
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = static_dir / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>OddStack not found</h1>"


@app.get("/api/auth-check")
def auth_check(user: str = Depends(check_auth)):
    return {"user": user}


@app.get("/analyze", response_model=AnalysisResult)
def analyze(
    num_legs: int = Query(default=None),
    stake: float = Query(default=None),
    limit: int = Query(default=100, description="Max combinations to return"),
    user: str = Depends(check_auth),
):
    try:
        n_legs = num_legs or Config.NUM_LEGS
        n_stake = stake or Config.STAKE
        matches = api_client.get_round_matches()
        engine = MultiplesEngine(num_legs=n_legs)
        result = engine.analyze(matches, stake=n_stake)
        if len(result.combinations) > limit:
            result.combinations = result.combinations[:limit]
        return result
    except Exception as e:
        logger.error("Error in /analyze: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute")
def execute(payload: ExecuteRequest, user: str = Depends(check_auth)):
    try:
        n_legs = payload.num_legs or Config.NUM_LEGS
        stake = payload.stake or Config.STAKE
        matches = api_client.get_round_matches()
        engine = MultiplesEngine(num_legs=n_legs)
        analysis = engine.analyze(matches, stake=stake)
        target_ranks = set(payload.combinations) if payload.combinations else set(range(1, min(len(analysis.combinations), 51)))
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
def execute_stream(payload: ExecuteRequest, user: str = Depends(check_auth)):
    try:
        n_legs = payload.num_legs or Config.NUM_LEGS
        stake = payload.stake or Config.STAKE
        matches = api_client.get_round_matches()
        engine = MultiplesEngine(num_legs=n_legs)
        analysis = engine.analyze(matches, stake=stake)
        target_ranks = set(payload.combinations) if payload.combinations else set(range(1, min(len(analysis.combinations), 51)))
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
def pending_bets(limit: int = 100, user: str = Depends(check_auth)):
    try:
        today = date.today()
        raw = api_client._get(f"/bets/history?status=pending&limit={limit}&date_start={(today - timedelta(days=30)).isoformat()}&date_end={today.isoformat()}")
        bets = raw.get("bets", [])
        grouped = {}
        for b in bets:
            tid = b.get("ticket_id", "?")
            if tid not in grouped:
                grouped[tid] = {
                    "ticket_id": tid,
                    "created_at": to_brt(b.get("created_at")),
                    "created_at_raw": b.get("created_at"),
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
                "odd": float(b.get("odd", 0)),
                "event_date": b.get("event_date"),
            })
        tickets = sorted(grouped.values(), key=lambda x: x.get("created_at_raw", ""), reverse=True)
        return {
            "tickets": tickets,
            "total_pending": len(tickets),
            "total_stake": sum(t["stake"] for t in tickets),
            "total_cashout_available": sum(1 for t in tickets if t["cashout_available"]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/bets/settled")
def settled_bets(limit: int = 50, user: str = Depends(check_auth)):
    try:
        today = date.today()
        raw = api_client._get(f"/bets/history?status=completed&limit={limit}&date_start={(today - timedelta(days=30)).isoformat()}&date_end={today.isoformat()}")
        bets = raw.get("bets", [])
        grouped = {}
        for b in bets:
            tid = b.get("ticket_id", "?")
            if tid not in grouped:
                grouped[tid] = {
                    "ticket_id": tid,
                    "created_at": to_brt(b.get("created_at")),
                    "created_at_raw": b.get("created_at"),
                    "status": b.get("bet_status_name"),
                    "stake": float(b.get("header_stake", 0)),
                    "total_odd": float(b.get("total_odd", 0)),
                    "payout": float(b.get("header_return", 0)) if b.get("bet_status_name") in ("Ganhou", "Ganho") else 0,
                    "selections": [],
                }
            grouped[tid]["selections"].append({
                "home": b.get("home"),
                "away": b.get("away"),
                "outcome": b.get("outcome_name"),
                "odd": float(b.get("odd", 0)),
            })
        tickets = sorted(grouped.values(), key=lambda x: x.get("created_at_raw", ""), reverse=True)
        return {"tickets": tickets}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ticket/{ticket_id}")
def ticket_detail(ticket_id: str, user: str = Depends(check_auth)):
    try:
        raw = api_client._get(f"/bets/details?ticket_id={ticket_id}")
        if isinstance(raw, dict):
            raw["created_at_brt"] = to_brt(raw.get("created_at", ""))
            if "stake" in raw:
                raw["stake"] = float(raw["stake"]) if isinstance(raw["stake"], str) else raw["stake"]
            if "total_odd" in raw:
                raw["total_odd"] = float(raw["total_odd"]) if isinstance(raw["total_odd"], str) else raw["total_odd"]
            if "potential_return" in raw:
                raw["potential_return"] = float(raw["potential_return"]) if isinstance(raw["potential_return"], str) else raw["potential_return"]
        return raw
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cashout")
def cashout(ticket_id: str = Query(...), total_cashout: float = Query(0), user: str = Depends(check_auth)):
    try:
        raw = api_client._post(f"/bets/cashout?ticket_id={ticket_id}&total_cashout={total_cashout}", data={})
        return raw
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status")
def status(user: str = Depends(check_auth)):
    try:
        balance = api_client.get_balance()
        return {"balance": float(balance), "config": {"num_legs": Config.NUM_LEGS, "stake": Config.STAKE, "between_bets_delay": Config.BETWEEN_BETS_DELAY}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/balance/stream")
def balance_stream(user: str = Depends(check_auth)):
    def generate():
        while True:
            try:
                balance = api_client.get_balance()
                yield f"data: {json.dumps({'balance': float(balance), 'time_brt': datetime.now(BRT).strftime('%H:%M:%S')})}\n\n"
            except Exception:
                yield f"data: {json.dumps({'balance': None, 'error': True})}\n\n"
            time.sleep(30)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/health")
def health():
    return {"status": "healthy"}


SHIELDS_CACHE = None


@app.get("/api/shields")
def get_shields():
    global SHIELDS_CACHE
    if SHIELDS_CACHE is None:
        try:
            import requests as req
            data = req.get("https://api.cartola.globo.com/clubes", timeout=10).json()
            SHIELDS_CACHE = {}
            for cid, club in data.items():
                name = (club.get("nome_fantasia") or "").strip()
                if name:
                    SHIELDS_CACHE[name.lower()] = club.get("escudos", {}).get("30x30", "")
        except Exception:
            SHIELDS_CACHE = {}
    return SHIELDS_CACHE


@app.get("/api/client-health")
def client_health():
    try:
        import requests as req
        r = req.get(f"{Config.BETNACIONAL_API_URL}/", timeout=5)
        ok = r.status_code == 200
        return {"status": "connected" if ok else "error", "http_code": r.status_code}
    except Exception as e:
        return {"status": "disconnected", "error": str(e)[:100]}


@app.get("/api/reconnect")
def reconnect():
    try:
        import requests as req
        r = req.get(f"{Config.BETNACIONAL_API_URL}/", timeout=5)
        if r.status_code == 200:
            return {"reconnected": True, "status": "Client já está online"}
        return {"reconnected": False, "status": f"HTTP {r.status_code}"}
    except Exception as e:
        msg = str(e)[:80]
        try:
            import subprocess
            subprocess.run(["docker", "restart", "betnacional-client"], capture_output=True, timeout=30)
            return {"reconnected": True, "status": "Container reiniciado. Aguarde 20s para login."}
        except Exception:
            return {"reconnected": False, "status": f"Cliente offline: {msg}. Reinicie manualmente."}
