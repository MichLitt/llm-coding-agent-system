"""Transparent aggregate metrics for raw ConversationBench v1 artifacts."""

from __future__ import annotations

from dataclasses import dataclass

from coder_agent.eval.conversation_runner import ConversationResult


@dataclass(frozen=True)
class Ratio:
    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None


@dataclass(frozen=True)
class ConversationMetrics:
    conversation_success: Ratio
    turn_pass: Ratio
    constraint_retention: Ratio
    regression_free_follow_up: Ratio
    completion_depth: Ratio


def compute_conversation_metrics(results: list[ConversationResult]) -> ConversationMetrics:
    successful = sum(result.success for result in results)
    phase_passed = phase_total = retained_passed = retained_total = 0
    follow_up_passed = follow_up_total = completed_turns = all_turns = 0
    for result in results:
        previous_phase_passed = False
        for index, turn in enumerate(result.turns):
            passed, total = turn.phase_checks["passed"], turn.phase_checks["total"]
            phase_passed += passed; phase_total += total
            applicable = turn.constraint_results
            retained_passed += sum(applicable.values()); retained_total += len(applicable)
            if index and previous_phase_passed:
                follow_up_total += 1
                follow_up_passed += int(passed == total and all(applicable.values()))
            previous_phase_passed = passed == total and all(applicable.values())
            completed_turns += 1
        # The denominator is declared task turns, avoiding a partial run looking complete.
        all_turns += result.total_turns
    return ConversationMetrics(
        Ratio(successful, len(results)), Ratio(phase_passed, phase_total),
        Ratio(retained_passed, retained_total), Ratio(follow_up_passed, follow_up_total),
        Ratio(completed_turns, all_turns),
    )
