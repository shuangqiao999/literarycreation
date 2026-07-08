"""Event Scheduler — milestone planning and event enforcement for blueprint-based writing.

For Mode B (outline execution), replaces the concept of "nudge-based" outline control
with graduated enforcement: hard events, soft goals, optional events.

Key decisions:
  - Hard events are enforced but LLM chooses motivation and style
  - Soft goals constrain the outcome, not the action
  - Optional events are suggested but skipped if contradictory
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ScheduledEvent:
    """A single event that the scheduler dispatches for a given round."""

    round: int
    description: str
    level: Literal["hard", "soft", "optional"] = "hard"
    participants: list[str] = field(default_factory=list)
    required_outcome: dict[str, Any] | None = None
    catch_up_window: int = 1
    max_retries: int = 2
    retries: int = 0


@dataclass
class Milestone:
    """Checkpoint between outline sections — granular enough for arc tracking."""

    round: int
    metrics: dict[str, float]           # target metrics at this milestone
    events: list[str]                   # key events in this segment
    tolerance: float = 10.0


@dataclass
class ChapterContext:
    """Unified data format passed to the prose renderer per chapter."""

    round_number: int
    mandatory_events: list[dict[str, Any]]
    soft_goals: list[dict[str, Any]]
    character_snapshots: dict[str, dict[str, float]]
    causal_chain: list[dict[str, Any]]
    narrative_phase: str


CorrectionLevel = Literal["none", "soft", "strong", "event_inject"]


class EventScheduler:
    """Orchestrates the conversion of outline → milestones → per-round events.

    Usage:
        scheduler = EventScheduler.from_outline(outline, total_rounds=20)
        events = scheduler.get_events_for_round(7)
        level = scheduler.check_correction(7, current_states, outline_spec)
    """

    def __init__(
        self,
        milestones: list[Milestone],
        key_events: list[ScheduledEvent],
        total_rounds: int = 10,
    ) -> None:
        self._milestones = sorted(milestones, key=lambda m: m.round)
        self._key_events = key_events
        self._total_rounds = max(1, total_rounds)

    @classmethod
    def from_outline(cls, outline: dict[str, Any], total_rounds: int) -> "EventScheduler":
        """Build scheduler from the outline dict provided in session config."""
        milestones = cls._build_milestones(outline, total_rounds)
        key_events = cls._build_key_events(outline)
        return cls(milestones, key_events, total_rounds)

    # ── milestone planning ──

    @staticmethod
    def _build_milestones(outline: dict[str, Any], total: int) -> list[Milestone]:
        """Build 3-5 milestones from character arc definitions.

        Without explicit milestone config, compute waypoints at 25%, 50%, 75%, 100%.
        """
        milestones: list[Milestone] = []
        chars = outline.get("characters", []) or []

        if total <= 0:
            return milestones

        checkpoints = [0.25, 0.50, 0.75, 1.0]
        for frac in checkpoints:
            rnd = max(1, int(total * frac))
            metrics: dict[str, float] = {}
            events: list[str] = []

            for c in chars:
                init = {k: float(v) for k, v in (c.get("initial_state") or {}).items()}
                final_s = {}
                for k, v in (c.get("final_state") or {}).items():
                    try:
                        final_s[k] = float(v)
                    except (ValueError, TypeError):
                        pass
                for key in set(init) | set(final_s):
                    iv = init.get(key, 50.0)
                    fv = final_s.get(key, iv)
                    metrics.setdefault(key, 0.0)
                    metrics[key] += iv + (fv - iv) * frac

            # Average across characters
            if chars:
                for key in metrics:
                    metrics[key] /= len(chars)

            milestones.append(Milestone(round=rnd, metrics=metrics, events=events))

        return milestones

    @staticmethod
    def _build_key_events(outline: dict[str, Any]) -> list[ScheduledEvent]:
        """Convert outline.key_events → ScheduledEvent list."""
        events: list[ScheduledEvent] = []
        for e in outline.get("key_events", []) or []:
            rnd = int(e.get("round", 1))
            description = str(e.get("event", e.get("description", "")))
            if not description:
                continue
            level = str(e.get("level", "hard"))
            events.append(ScheduledEvent(
                round=rnd,
                description=description,
                level=level if level in ("hard", "soft", "optional") else "hard",
                required_outcome=e.get("required_outcome"),
            ))
        return events

    # ── per-round dispatch ──

    def get_events_for_round(self, round_number: int) -> list[ScheduledEvent]:
        """Collect events scheduled for this round (including catch-up).

        Soft/optional events that have been retried too many times are dropped.
        """
        results: list[ScheduledEvent] = []
        for event in self._key_events:
            window_start = max(1, event.round - event.catch_up_window)
            if window_start <= round_number <= event.round:
                if event.level == "optional" and event.retries >= event.max_retries:
                    continue
                results.append(event)
        return results

    def mark_event_triggered(self, event: ScheduledEvent) -> None:
        """Record that an event was successfully dispatched."""
        event.retries = 0

    def mark_event_postponed(self, event: ScheduledEvent) -> None:
        """Record a retry attempt. After max_retries, hard events still fire."""
        event.retries += 1
        if event.level == "hard":
            event.catch_up_window += 1

    # ── correction evaluation ──

    def check_correction(
        self,
        round_number: int,
        states: dict[str, Any],
        outline_spec: dict[str, Any] | None = None,
    ) -> CorrectionLevel:
        """Determine correction strength based on deviation from closest milestone.

        Returns one of: "none", "soft", "strong", "event_inject"
        """
        # Find the nearest milestone
        target = self._find_closest_milestone(round_number)
        if not target:
            return "none"

        max_gap = 0.0
        tolerance = target.tolerance
        for name, spec in (outline_spec or {}).items():
            init = {k: float(v) for k, v in (spec.get("initial_state") or {}).items()}
            for metric, tgt in target.metrics.items():
                actual = float(states.get(name, {}).metrics.get(metric, 0.0))
                expected = float(init.get(metric, 50.0))
                # Linear interpolation: where we should be at this milestone
                expected = expected + (tgt - expected) * min(1.0, round_number / max(1, target.round))
                gap = abs(actual - expected)
                max_gap = max(max_gap, gap)

        if max_gap < tolerance:
            return "none"
        if max_gap < tolerance * 2:
            return "soft"
        if max_gap < tolerance * 3:
            return "strong"
        return "event_inject"

    def get_narrative_phase(self, round_number: int) -> str:
        """Classify the current narrative phase based on position in total rounds."""
        fraction = round_number / max(1, self._total_rounds)
        if fraction < 0.25:
            return "exposition"
        if fraction < 0.45:
            return "rising_action"
        if fraction < 0.65:
            return "climax_zone"
        if fraction < 0.80:
            return "falling_action"
        return "resolution"

    def build_chapter_context(
        self,
        round_number: int,
        states: dict[str, Any],
        causal_chain: list[dict[str, Any]],
        outline_spec: dict[str, Any] | None = None,
    ) -> ChapterContext:
        """Build the unified data structure for prose rendering."""
        events = self.get_events_for_round(round_number)
        mandatory = []
        soft_goals = []
        for e in events:
            if e.level == "hard":
                mandatory.append({"description": e.description, "participants": e.participants})
            elif e.level == "soft":
                soft_goals.append({"description": e.description, "outcome": e.required_outcome})

        snapshots: dict[str, dict[str, float]] = {}
        for eid, st in states.items():
            if hasattr(st, "name") and hasattr(st, "metrics"):
                snapshots[st.name] = {k: float(v) for k, v in st.metrics.items()}

        return ChapterContext(
            round_number=round_number,
            mandatory_events=mandatory,
            soft_goals=soft_goals,
            character_snapshots=snapshots,
            causal_chain=causal_chain,
            narrative_phase=self.get_narrative_phase(round_number),
        )

    def get_mandate_text(self, round_number: int) -> str:
        """Build the imperative prompt text for hard events this round."""
        events = self.get_events_for_round(round_number)
        hard = [e for e in events if e.level == "hard"]
        if not hard:
            return ""
        return "；".join(e.description for e in hard)

    def get_soft_goals_text(self, round_number: int) -> str:
        """Build the advisory prompt text for soft goals this round."""
        events = self.get_events_for_round(round_number)
        soft = [e for e in events if e.level == "soft"]
        if not soft:
            return ""
        parts = []
        for e in soft:
            parts.append(e.description)
        return "；".join(parts)

    # ── internal ──

    def _find_closest_milestone(self, round_number: int) -> Milestone | None:
        """Find the milestone whose round is <= current round, closest."""
        best: Milestone | None = None
        for m in self._milestones:
            if m.round <= round_number:
                best = m
            else:
                break
        return best
