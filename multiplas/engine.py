import logging
from itertools import combinations, product
from typing import List

from multiplas.models import MatchOdds, Combination, AnalysisResult

logger = logging.getLogger("multiplas.engine")

OUTCOME_LABELS = {"1": "casa", "2": "empate", "3": "fora"}


class MultiplesEngine:
    def __init__(self, num_legs: int = 3):
        self.num_legs = num_legs

    def analyze(self, matches: List[MatchOdds], stake: float = 1.0) -> AnalysisResult:
        if len(matches) < self.num_legs:
            return AnalysisResult(
                num_matches=len(matches),
                num_legs=self.num_legs,
                total_combinations=0,
                stake_per_bet=stake,
                total_stake=0,
                combinations=[]
            )

        all_combos: List[Combination] = []

        for match_combo in combinations(matches, self.num_legs):
            outcome_lists = []
            for m in match_combo:
                outcome_lists.append([
                    (m, o) for o in m.outcomes
                ])

            for outcome_combo in product(*outcome_lists):
                entries = []
                combined_prob = 1.0
                total_odd = 1.0

                for match, outcome in outcome_combo:
                    entries.append({
                        "match_id": match.match_id,
                        "choice": OUTCOME_LABELS.get(outcome.id, outcome.id),
                        "team": outcome.name,
                        "odd": outcome.odd,
                        "implied_prob": outcome.implied_prob,
                        "match": f"{match.home_team} vs {match.away_team}",
                        "start_time": match.start_time,
                    })
                    combined_prob *= outcome.implied_prob
                    total_odd *= outcome.odd

                all_combos.append(Combination(
                    rank=0,
                    combined_prob=round(combined_prob, 6),
                    total_odd=round(total_odd, 2),
                    expected_return=round(total_odd * stake, 2),
                    entries=entries,
                    matches_summary=" | ".join(
                        f"{e['team']} (@{e['odd']})"
                        for e in entries
                    ),
                ))

        all_combos.sort(key=lambda c: c.combined_prob, reverse=True)
        for i, c in enumerate(all_combos):
            c.rank = i + 1

        total_stake = len(all_combos) * stake

        logger.info(
            "Analysis: %d matches, %d legs = %d combos, total stake R$%.2f",
            len(matches), self.num_legs, len(all_combos), total_stake
        )

        return AnalysisResult(
            num_matches=len(matches),
            num_legs=self.num_legs,
            total_combinations=len(all_combos),
            stake_per_bet=stake,
            total_stake=total_stake,
            combinations=all_combos
        )
