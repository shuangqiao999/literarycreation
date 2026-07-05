"""Smoke test for algorithm modules — literary domain."""
import numpy as np
from literarycreation.algorithms import ModuleContext
from literarycreation.algorithms.fsm_module import FiniteStateMachineModule
from literarycreation.algorithms.outline_control import OutlineControlModule
from literarycreation.algorithms.pacing_analyzer import PacingAnalyzerModule
from literarycreation.algorithms.character_consistency import CharacterConsistencyModule
from literarycreation.algorithms.conflict_progression import ConflictProgressionModule

# Test FSM: character state transitions
ctx = ModuleContext(round_number=1)
ctx.arrays = {
    "tension": np.array([80.0, 30.0], dtype=np.float64),
    "trust": np.array([40.0, 60.0], dtype=np.float64),
    "affection": np.array([30.0, 75.0], dtype=np.float64),
}
ctx.metadata["entity_ids"] = ["char_a", "char_b"]
ctx.metadata["entity_names"] = ["Zhang San", "Li Si"]

fsm = FiniteStateMachineModule()
fsm.configure({
    "default_state": "neutral",
    "command_states": ["crisis"],
    "transition_rules": [
        {"from": "neutral", "to": "crisis", "condition": {"tension": [">", 70]}},
        {"from": "neutral", "to": "intimate", "condition": {"affection": [">", 70], "trust": [">", 50]}},
    ],
    "action_map": {
        "neutral": {"action_type": "observe", "intensity": 0.3},
        "crisis": None,
        "intimate": {"action_type": "confess", "intensity": 0.7},
    },
})
ctx = fsm.execute(ctx)
assert ctx.metadata["fsm.agent_states"][0] == "crisis", f"Expected crisis, got {ctx.metadata['fsm.agent_states'][0]}"
assert ctx.metadata["fsm.agent_states"][1] == "intimate", f"Expected intimate, got {ctx.metadata['fsm.agent_states'][1]}"
assert len(ctx.metadata["fsm.agent_actions"]) == 2
assert ctx.metadata["fsm.agent_actions"][0] is None  # crisis -> LLM
print(f"FSM: states={ctx.metadata['fsm.agent_states']} OK")

# Test Outline Control
ctx2 = ModuleContext(round_number=5)
ctx2.arrays = {"trust": np.array([60.0], dtype=np.float64), "tension": np.array([30.0], dtype=np.float64)}
ctx2.metadata["entity_names"] = ["Zhang San"]

oc = OutlineControlModule()
oc.configure({
    "deviation_threshold": 12.0,
    "total_rounds": 10,
    "outline": {"characters": [
        {"name": "Zhang San", "initial_state": {"trust": 80, "tension": 20},
         "final_state": {"trust": 20, "tension": 80}},
    ]},
})
ctx2 = oc.execute(ctx2)
nudges = ctx2.metadata.get("outline.nudges", [])
print(f"Outline Control: nudges={nudges} OK")

# Test Pacing Analyzer
ctx3 = ModuleContext(round_number=1)
ctx3.arrays = {"tension": np.array([40.0, 60.0, 80.0], dtype=np.float64), "trust": np.array([50.0, 50.0, 50.0], dtype=np.float64)}
pa = PacingAnalyzerModule()
pa.configure({"stall_threshold": 3, "rush_threshold": 30.0, "plateau_rounds": 4})
ctx3 = pa.execute(ctx3)
ctx3.round_number = 2
ctx3.arrays["tension"] = np.array([90.0, 65.0, 85.0], dtype=np.float64)
ctx3 = pa.execute(ctx3)
score = ctx3.metadata.get("pacing.score", 0)
print(f"Pacing Analyzer: score={score} OK")
assert score is not None

# Test Character Consistency
ctx4 = ModuleContext(round_number=1)
ctx4.arrays = {"trust": np.array([80.0], dtype=np.float64), "affection": np.array([50.0], dtype=np.float64)}
ctx4.metadata["entity_ids"] = ["char_a"]
ctx4.metadata["entity_names"] = ["Zhang San"]
ctx4.metadata["consistency.decisions"] = [{"actor_id": "char_a", "action_type": "betray"}]

cc = CharacterConsistencyModule()
cc.configure({"warn_threshold": 0.7})
ctx4 = cc.execute(ctx4)
flags = ctx4.metadata.get("consistency.flags", [])
print(f"Character Consistency: flags={flags} OK")

# Test Conflict Progression
ctx5 = ModuleContext(round_number=5)
ctx5.arrays = {"tension": np.array([80.0, 70.0], dtype=np.float64)}

cp = ConflictProgressionModule()
cp.configure({"total_rounds": 10, "climax_min_tension": 65.0, "early_drop_threshold": 30.0})
cp._tension_history = [20.0, 30.0, 45.0, 65.0]
ctx5 = cp.execute(ctx5)
phase = ctx5.metadata.get("conflict.arc_phase", "")
warnings = ctx5.metadata.get("conflict.warnings", [])
print(f"Conflict Progression: phase={phase}, warnings={warnings} OK")

print("\nALL ALGORITHM TESTS PASSED")
