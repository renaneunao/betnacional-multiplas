from pydantic import BaseModel
from typing import List, Optional


class Outcome(BaseModel):
    id: str
    name: str
    odd: float
    implied_prob: float


class MatchOdds(BaseModel):
    match_id: str
    home_team: str
    away_team: str
    start_time: str
    outcomes: List[Outcome]
    margin: float


class Combination(BaseModel):
    rank: int
    combined_prob: float
    total_odd: float
    expected_return: float
    entries: List[dict]
    matches_summary: str


class AnalysisResult(BaseModel):
    num_matches: int
    num_legs: int
    total_combinations: int
    stake_per_bet: float
    total_stake: float
    combinations: List[Combination]


class ExecuteRequest(BaseModel):
    combinations: Optional[List[int]] = None
    stake: Optional[float] = None
    num_legs: Optional[int] = None


class BetResult(BaseModel):
    success: bool
    bet_id: Optional[str] = None
    message: Optional[str] = None
