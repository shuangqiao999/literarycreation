"""Smoke test for reconstructed literary engine."""
import numpy as np
from literarycreation.algorithms import compute_deviation, resolve_correction, build_correction_prompt
from literarycreation.engine.event_scheduler import EventScheduler, ScheduledEvent

# Test deviation computation
gap = compute_deviation("trust", 50.0, 80.0, 20.0, 5, 10, tolerance=10.0)
print(f"Deviation: trust gap={gap:.1f} (current=50, target should be ~50)")

gap2 = compute_deviation("trust", 80.0, 80.0, 20.0, 1, 10, tolerance=10.0)
print(f"Deviation: trust gap={gap2:.1f} (current=80, target should be ~74)")

# Test correction resolution
level = resolve_correction({"trust": 5.0, "tension": 8.0}, tolerance=10.0)
assert level == "none", f"Expected none, got {level}"
print(f"Small gaps → {level} OK")

level2 = resolve_correction({"trust": 25.0}, tolerance=10.0)
assert level2 == "strong", f"Expected strong, got {level2}"
print(f"Large gap → {level2} OK")

level3 = resolve_correction({"trust": 35.0}, tolerance=10.0)
assert level3 == "event_inject", f"Expected event_inject, got {level3}"
print(f"Very large gap → {level3} OK")

# Test correction prompt
prompt = build_correction_prompt({0: {"trust": 25.0}}, "strong", {0: "Zhang San"})
assert "强" in prompt, f"Prompt should be strong directive: {prompt}"
print(f"Correction prompt: {prompt[:60]}... OK")

# Test event scheduler
outline = {
    "key_events": [
        {"round": 3, "event": "凌不弃密报沈夜通敌", "level": "hard"},
        {"round": 5, "event": "沈夜开始怀疑凌不弃", "level": "soft"},
        {"round": 7, "event": "可有可无的事件", "level": "optional"},
    ],
    "characters": [
        {"name": "凌不弃", "initial_state": {"trust": 80}, "final_state": {"trust": 20}},
    ],
}
scheduler = EventScheduler.from_outline(outline, 10)
events = scheduler.get_events_for_round(3)
hard_events = [e for e in events if e.level == "hard"]
assert len(hard_events) == 1, f"Expected 1 hard event, got {len(hard_events)}"
print(f"Scheduler round 3: {hard_events[0].description} OK")

mandate = scheduler.get_mandate_text(3)
assert "密报" in mandate, f"Mandate should contain event text: {mandate}"
print(f"Mandate: {mandate} OK")

# Test catch-up window
events5 = scheduler.get_events_for_round(5)
soft = [e for e in events5 if e.level == "soft"]
assert len(soft) == 1, f"Expected 1 soft event, got {len(soft)}"
print(f"Scheduler round 5: {soft[0].description} OK")

print("\nALL TESTS PASSED")
