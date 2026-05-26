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
from multiplas.cartola import cartola_to_betnacional_name

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
    require_draw: bool = Query(default=False, description="Only combos with at least one draw"),
    max_odd: float = Query(default=None, description="Filter outcomes with odd > this value"),
    mixed: int = Query(default=None, description="Select N diverse combos across probability tiers"),
    min_fav_odd: float = Query(default=None, description="Remove matches where favorite odd > this"),
    rodada: int = Query(default=None, description="Cartola round number"),
    user: str = Depends(check_auth),
):
    try:
        n_legs = num_legs or Config.NUM_LEGS
        n_stake = stake or Config.STAKE
        matches = api_client.get_round_matches(rodada=rodada)
        engine = MultiplesEngine(num_legs=n_legs)
        result = engine.analyze(matches, stake=n_stake, require_draw=require_draw, max_odd=max_odd, mixed_count=mixed, min_favorite_odd=min_fav_odd)
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
        if isinstance(raw, dict) and not raw.get("found"):
            raw2 = api_client._get(f"/bets/history?status=completed&limit=50&date_start={(date.today() - timedelta(days=90)).isoformat()}&date_end={date.today().isoformat()}")
            bets = raw2.get("bets", [])
            ticket_bets = [b for b in bets if b.get("ticket_id") == ticket_id]
            if not ticket_bets:
                return {"ticket_id": ticket_id, "found": False, "selections": []}
            header = ticket_bets[0]
            selections = [{
                "event_id": b.get("event_id"), "home": b.get("home"), "away": b.get("away"),
                "market_name": b.get("market_name"), "outcome_name": b.get("outcome_name"),
                "odd": b.get("odd"), "current_odd": b.get("current_odd"),
            } for b in ticket_bets]
            raw = {
                "ticket_id": ticket_id, "found": True,
                "stake": float(header.get("header_stake", 0)), "total_odd": float(header.get("total_odd", 0)),
                "potential_return": float(header.get("header_return", 0)),
                "status": header.get("bet_status_name"), "cashout_available": False,
                "created_at": header.get("created_at"), "selections": selections,
            }
        if isinstance(raw, dict):
            raw["created_at_brt"] = to_brt(raw.get("created_at", ""))
            if "stake" in raw: raw["stake"] = float(raw["stake"]) if isinstance(raw["stake"], str) else raw["stake"]
            if "total_odd" in raw: raw["total_odd"] = float(raw["total_odd"]) if isinstance(raw["total_odd"], str) else raw["total_odd"]
            if "potential_return" in raw: raw["potential_return"] = float(raw["potential_return"]) if isinstance(raw["potential_return"], str) else raw["potential_return"]
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
def balance_stream(token: str = Query(default="")):
    valid = False
    if token:
        import base64
        try:
            decoded = base64.b64decode(token).decode()
            user, passwd = decoded.split(":", 1)
            valid = secrets.compare_digest(user, DASH_USER) and secrets.compare_digest(passwd, DASH_PASS)
        except Exception:
            pass
    if not valid:
        def bad():
            yield f"data: {json.dumps({'error': 'unauthorized'})}\n\n"
        return StreamingResponse(bad(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}, status_code=200)

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
                shield = club.get("escudos", {}).get("30x30", "")
                if name and shield:
                    SHIELDS_CACHE[name.lower()] = shield
            ALIASES = {
                "red bull bragantino": "bragantino",
                "vasco da gama": "vasco",
                "athletico pr": "athlético-pr",
                "atlético mg": "atlético-mg",
                "atletico mg": "atlético-mg",
                "são paulo": "são paulo",
            }
            for alias, target in ALIASES.items():
                if target in SHIELDS_CACHE and alias not in SHIELDS_CACHE:
                    SHIELDS_CACHE[alias] = SHIELDS_CACHE[target]
        except Exception:
            SHIELDS_CACHE = {}
    return SHIELDS_CACHE


@app.get("/api/shadow-test")
def shadow_test(
    rodada: int = Query(default=18),
    num_legs: int = Query(default=10),
    stake: float = Query(default=1.0),
    require_draw: bool = Query(default=False),
    max_odd: float = Query(default=None),
    min_fav_odd: float = Query(default=None),
    max_combos: int = Query(default=500),
    user: str = Depends(check_auth),
):
    try:
        import requests as req
        cartola_url = f"https://api.cartola.globo.com/partidas/{rodada}"
        cartola_data = req.get(cartola_url, timeout=15).json()
        partidas = cartola_data.get("partidas", [])
        clubes = cartola_data.get("clubes", {})

        results = {}
        for p in partidas:
            if p.get("valida") and p.get("placar_oficial_mandante") is not None:
                casa_id = str(p.get("clube_casa_id"))
                visitante_id = str(p.get("clube_visitante_id"))
                casa_name = (clubes.get(casa_id, {}) or {}).get("nome_fantasia", "")
                visitante_name = (clubes.get(visitante_id, {}) or {}).get("nome_fantasia", "")
                gols_casa = p.get("placar_oficial_mandante") or 0
                gols_visitante = p.get("placar_oficial_visitante") or 0
                if gols_casa > gols_visitante:
                    resultado = "casa"
                elif gols_casa < gols_visitante:
                    resultado = "fora"
                else:
                    resultado = "empate"
                results[f"{casa_name}||{visitante_name}"] = {
                    "casa": casa_name, "visitante": visitante_name,
                    "placar": f"{gols_casa}x{gols_visitante}",
                    "resultado": resultado,
                }

        if len(results) < 2:
            return {"error": "Rodada não tem jogos encerrados suficientes.", "jogos_encerrados": len(results)}

        matches = api_client.get_matches()
        engine = MultiplesEngine(num_legs=num_legs)
        analysis = engine.analyze(matches, stake=stake, require_draw=require_draw, max_odd=max_odd, min_favorite_odd=min_fav_odd)

        total = len(analysis.combinations)
        won = 0
        winning_combos = []

        for combo in analysis.combinations[:max_combos]:
            all_correct = True
            combo_matches = []
            for e in combo.entries:
                match_key = f"{e['match']}"
                found = False
                for rkey, rval in results.items():
                    cartola_casa = cartola_to_betnacional_name(rval["casa"])
                    cartola_visit = cartola_to_betnacional_name(rval["visitante"])
                    if (cartola_casa in e["match"] or e["match"].split(" vs ")[0] in cartola_casa) and \
                       (cartola_visit in e["match"] or e["match"].split(" vs ")[1] in cartola_visit):
                        if e["choice"] != rval["resultado"]:
                            all_correct = False
                        combo_matches.append({
                            "match": e["match"], "choice": e["choice"],
                            "result": rval["resultado"], "placar": rval["placar"],
                            "hit": e["choice"] == rval["resultado"],
                        })
                        found = True
                        break
                if not found:
                    all_correct = False
            if all_correct and len(combo_matches) == num_legs:
                won += 1
                if len(winning_combos) < 10:
                    winning_combos.append({
                        "rank": combo.rank, "matches": combo.matches_summary,
                        "total_odd": combo.total_odd, "combined_prob": combo.combined_prob,
                        "details": combo_matches,
                    })

        return {
            "rodada": rodada,
            "jogos_encerrados": len(results),
            "resultados": [{"casa": r["casa"], "visitante": r["visitante"], "placar": r["placar"], "resultado": r["resultado"]} for r in results.values()],
            "total_combos": total,
            "combos_avaliados": min(total, max_combos),
            "vencedores": won,
            "taxa_acerto_pct": round(won / max(1, min(total, max_combos)) * 100, 2),
            "stake_total": min(total, max_combos) * stake,
            "retorno_estimado": round(won * (sum(c.get("total_odd", 0) for c in winning_combos) / max(1, len(winning_combos))), 2) if winning_combos else 0,
            "top_vencedores": winning_combos,
        }
    except Exception as e:
        logger.error("Shadow test error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


SWEEP_CACHE = None


@app.get("/api/shadow-sweep")
def shadow_sweep(user: str = Depends(check_auth)):
    global SWEEP_CACHE
    if SWEEP_CACHE is not None and SWEEP_CACHE.get("_ts", 0) > time.time() - 3600:
        return SWEEP_CACHE

    import requests as req
    from itertools import combinations, product

    rounds = list(range(1, 18))
    legs_list = [3, 4, 5, 10]
    configs = [
        {"draw": False, "balanced": False},
        {"draw": True, "balanced": False},
        {"draw": False, "balanced": True},
        {"draw": True, "balanced": True},
    ]
    results = []
    total_tests = len(rounds) * len(legs_list) * len(configs)

    for rodada in rounds:
        try:
            cartola = req.get(f"https://api.cartola.globo.com/partidas/{rodada}", timeout=15).json()
        except Exception:
            continue
        clubes = cartola.get("clubes", {})
        partidas = cartola.get("partidas", [])
        games = []
        for p in partidas:
            if p.get("valida") and p.get("placar_oficial_mandante") is not None:
                casa_id = str(p.get("clube_casa_id"))
                visit_id = str(p.get("clube_visitante_id"))
                casa = (clubes.get(casa_id, {}) or {}).get("nome_fantasia", "")
                visit = (clubes.get(visit_id, {}) or {}).get("nome_fantasia", "")
                gols_c = p.get("placar_oficial_mandante") or 0
                gols_v = p.get("placar_oficial_visitante") or 0
                if gols_c > gols_v: res = "casa"
                elif gols_v > gols_c: res = "fora"
                else: res = "empate"
                form_casa = p.get("aproveitamento_mandante", [])
                balanced = False
                if form_casa:
                    wins = sum(1 for x in form_casa if x == "v")
                    balanced = wins <= 2
                games.append({"casa": casa, "visit": visit, "result": res, "balanced": balanced, "placar": f"{gols_c}x{gols_v}"})

        if len(games) < 6:
            continue

        for legs in legs_list:
            if legs > len(games): continue
            for cfg in configs:
                total_combos = 0
                won = 0
                winning_examples = []
                for match_combo in combinations(games, legs):
                    if cfg["balanced"]:
                        if any(not g["balanced"] for g in match_combo):
                            continue
                    outcome_lists = [[(g, r) for r in ["casa", "empate", "fora"]] for g in match_combo]
                    for outcome_combo in product(*outcome_lists):
                        if cfg["draw"]:
                            if not any(o[1] == "empate" for o in outcome_combo):
                                continue
                        total_combos += 1
                        if total_combos > 500:
                            break
                        if all(o[0]["result"] == o[1] for o in outcome_combo):
                            won += 1
                            if len(winning_examples) < 3:
                                winning_examples.append({
                                    "picks": [{"casa": o[0]["casa"], "visit": o[0]["visit"], "pick": o[1], "result": o[0]["result"], "placar": o[0]["placar"]} for o in outcome_combo]
                                })
                    if total_combos > 500:
                        break

                pct = round(won / max(1, total_combos) * 100, 2)
                results.append({
                    "rodada": rodada, "legs": legs,
                    "draw": cfg["draw"], "balanced": cfg["balanced"],
                    "games": len(games), "combos": total_combos,
                    "won": won, "pct": pct,
                    "entradas_para_1_acerto": max(1, round(total_combos / max(1, won))) if won > 0 else None,
                    "winning_examples": winning_examples,
                })

    if not results:
        return {"error": "Nenhum resultado gerado."}

    best = max(results, key=lambda r: (r["pct"], -(r.get("entradas_para_1_acerto") or 99999)))

    by_legs = {}
    for r in results:
        k = str(r["legs"])
        if k not in by_legs: by_legs[k] = {"pcts": [], "entradas": []}
        by_legs[k]["pcts"].append(r["pct"])
        if r.get("entradas_para_1_acerto"):
            by_legs[k]["entradas"].append(r["entradas_para_1_acerto"])

    by_draw = {"with": [], "without": []}
    by_balanced = {"with": [], "without": []}
    for r in results:
        (by_draw["with"] if r["draw"] else by_draw["without"]).append(r["pct"])
        (by_balanced["with"] if r["balanced"] else by_balanced["without"]).append(r["pct"])

    avg = lambda lst: round(sum(lst) / len(lst), 2) if lst else 0

    insights = {
        "best_config": best,
        "best_desc": f"{best['legs']} pernas, " +
                     ("com empate, " if best['draw'] else "sem empate, ") +
                     ("sem equilibrados" if best['balanced'] else "todos os jogos"),
        "by_legs": {k: {"avg_pct": avg(v["pcts"]), "avg_entradas": avg(v["entradas"])} for k, v in by_legs.items()},
        "draw_impact": {"with_avg": avg(by_draw["with"]), "without_avg": avg(by_draw["without"])},
        "balanced_impact": {"with_avg": avg(by_balanced["with"]), "without_avg": avg(by_balanced["without"])},
        "total_tests": total_tests,
        "tests_run": len(results),
        "recommendation": (
            f"Use {best['legs']} pernas, " +
            ("exija pelo menos 1 empate, " if best['draw'] else "não exija empates, ") +
            ("remova jogos equilibrados. " if best['balanced'] else "mantenha todos os jogos. ") +
            f"Com essa config, ~{best.get('entradas_para_1_acerto') or '?'} entradas para 1 acerto ({best['pct']}% taxa)."
        ),
    }

    SWEEP_CACHE = {"insights": insights, "results": results, "_ts": time.time()}
    return SWEEP_CACHE

    import requests as req
    rounds = list(range(13, 18))
    legs_list = [3, 4, 5, 10]
    configs = [
        {"draw": False, "balanced": False},
        {"draw": True, "balanced": False},
        {"draw": False, "balanced": True},
        {"draw": True, "balanced": True},
    ]
    max_combos = 30
    results = []
    total_tests = len(rounds) * len(legs_list) * len(configs)
    done = 0

    matches = api_client.get_matches()

    for rodada in rounds:
        try:
            cartola_url = f"https://api.cartola.globo.com/partidas/{rodada}"
            cartola_data = req.get(cartola_url, timeout=15).json()
        except Exception:
            continue
        clubes = cartola_data.get("clubes", {})
        partidas = cartola_data.get("partidas", [])
        match_results = {}
        for p in partidas:
            if p.get("valida") and p.get("placar_oficial_mandante") is not None:
                casa_id = str(p.get("clube_casa_id"))
                visitante_id = str(p.get("clube_visitante_id"))
                casa_name = (clubes.get(casa_id, {}) or {}).get("nome_fantasia", "")
                visitante_name = (clubes.get(visitante_id, {}) or {}).get("nome_fantasia", "")
                gols_casa = p.get("placar_oficial_mandante") or 0
                gols_visitante = p.get("placar_oficial_visitante") or 0
                if gols_casa > gols_visitante: resultado = "casa"
                elif gols_casa < gols_visitante: resultado = "fora"
                else: resultado = "empate"
                match_results[f"{casa_name}||{visitante_name}"] = resultado

        if len(match_results) < 4:
            continue

        for legs in legs_list:
            for cfg in configs:
                done += 1
                try:
                    engine = MultiplesEngine(num_legs=legs)
                    analysis = engine.analyze(
                        matches, stake=1.0, require_draw=cfg["draw"],
                        min_favorite_odd=1.85 if cfg["balanced"] else None,
                    )
                    won = 0
                    for combo in analysis.combinations[:max_combos]:
                        correct = True
                        for e in combo.entries:
                            found_match = False
                            for rkey, rresult in match_results.items():
                                cartola_casa = cartola_to_betnacional_name(rkey.split("||")[0])
                                cartola_visit = cartola_to_betnacional_name(rkey.split("||")[1])
                                e_casa = e["match"].split(" vs ")[0]
                                e_visit = e["match"].split(" vs ")[1] if " vs " in e["match"] else ""
                                if (cartola_casa in e["match"] or e_casa in cartola_casa) and \
                                   (cartola_visit in e["match"] or e_visit in cartola_visit):
                                    found_match = True
                                    if e["choice"] != rresult:
                                        correct = False
                                    break
                            if not found_match:
                                correct = False
                        if correct:
                            won += 1
                    pct = round(won / max(1, min(len(analysis.combinations), max_combos)) * 100, 2)
                    results.append({
                        "rodada": rodada, "legs": legs,
                        "draw": cfg["draw"], "balanced": cfg["balanced"],
                        "combos": min(len(analysis.combinations), max_combos),
                        "won": won, "pct": pct,
                    })
                except Exception:
                    pass

    if not results:
        return {"error": "Nenhum resultado gerado."}

    best = max(results, key=lambda r: r["pct"])
    by_legs = {}
    for r in results:
        key = str(r["legs"])
        if key not in by_legs:
            by_legs[key] = {"total": 0, "won": 0, "count": 0}
        by_legs[key]["total"] += 1
        by_legs[key]["won"] += r["won"]
        by_legs[key]["count"] += 1

    by_draw = {"with": [], "without": []}
    by_balanced = {"with": [], "without": []}
    for r in results:
        if r["draw"]: by_draw["with"].append(r["pct"])
        else: by_draw["without"].append(r["pct"])
        if r["balanced"]: by_balanced["with"].append(r["pct"])
        else: by_balanced["without"].append(r["pct"])

    avg = lambda lst: round(sum(lst) / len(lst), 2) if lst else 0

    insights = {
        "best_config": best,
        "best_desc": f"{best['legs']} pernas, " +
                     ("com empate, " if best['draw'] else "sem empate, ") +
                     ("sem equilibrados" if best['balanced'] else "todos os jogos"),
        "by_legs": {k: {"avg_pct": round(v["won"] / max(1, v["total"] * max_combos) * 100, 2)} for k, v in by_legs.items()},
        "draw_impact": {"with_avg": avg(by_draw["with"]), "without_avg": avg(by_draw["without"])},
        "balanced_impact": {"with_avg": avg(by_balanced["with"]), "without_avg": avg(by_balanced["without"])},
        "total_tests": total_tests,
        "tests_run": len(results),
        "recommendation": (
            f"Use {best['legs']} pernas, " +
            ("exija pelo menos 1 empate, " if best["draw"] else "não exija empates, ") +
            ("remova jogos equilibrados. " if best["balanced"] else "mantenha todos os jogos. ") +
            f"Taxa de acerto com essa config: {best['pct']}% em {best['combos']} combos."
        ),
    }

    SWEEP_CACHE = {"insights": insights, "results": results, "_ts": time.time()}
    return SWEEP_CACHE


@app.get("/api/client-health")
def client_health():
    try:
        import requests as req
        r = req.get(f"{Config.BETNACIONAL_API_URL}/balance", timeout=5)
        if r.status_code == 200:
            data = r.json()
            if "balance" in data and "detail" not in data:
                return {"status": "connected", "balance": data.get("balance")}
        return {"status": "error", "http_code": r.status_code, "detail": str(r.text)[:100]}
    except Exception as e:
        return {"status": "disconnected", "error": str(e)[:100]}


@app.get("/api/reconnect")
def reconnect():
    import json as _json
    try:
        import requests as req
        r = req.get(f"{Config.BETNACIONAL_API_URL}/balance", timeout=5)
        if r.status_code == 200 and "balance" in r.json():
            return {"reconnected": True, "status": "Cliente já está online"}
    except Exception:
        pass
    try:
        import http.client
        sock_path = "/var/run/docker.sock"
        conn = http.client.HTTPConnection("localhost", timeout=30)
        conn.sock = None
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(sock_path)
        conn.sock = s
        conn.request("POST", "/v1.47/containers/betnacional-client/restart")
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()
        if resp.status in (200, 204):
            return {"reconnected": True, "status": "Container betnacional-client reiniciado. Aguarde ~25s para o login."}
        return {"reconnected": False, "status": f"Docker API respondeu {resp.status}: {body[:100]}"}
    except Exception as e:
        return {"reconnected": False, "status": f"Não foi possível reiniciar: {str(e)[:100]}"}
