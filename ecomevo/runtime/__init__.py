from .engine import EcomEvoEngine
from .event_store import EventStore
from .planner import AdaptivePlanner
from .verifier import DecisionVerifier
from .sandbox import ActionSandbox
from .tools import ToolRegistry, PTCExecutor
from .evolver import FailureDrivenEvolver
from .autonomy import AutonomousController, TaskGraph
from .skills import AdaptiveSkillLibrary
from .adaptive_routing import AdaptiveAutonomousController, AdaptiveDecisionPolicy, AdaptiveRoutingStore
from .counterfactual_routing import CounterfactualAdaptiveAutonomousController, CounterfactualAdaptiveDecisionPolicy

# EcomEvoEngine resolves AutonomousController from its module globals at construction time.
# Bind production runs to the counterfactual adaptive controller while keeping the base
# classes importable for deterministic fallback and focused regression work.
from . import engine as _engine
_engine.AutonomousController = CounterfactualAdaptiveAutonomousController

__all__=[
    'EcomEvoEngine','EventStore','AdaptivePlanner','DecisionVerifier','ActionSandbox',
    'ToolRegistry','PTCExecutor','FailureDrivenEvolver','AutonomousController','TaskGraph',
    'AdaptiveSkillLibrary','AdaptiveAutonomousController','AdaptiveDecisionPolicy','AdaptiveRoutingStore',
    'CounterfactualAdaptiveAutonomousController','CounterfactualAdaptiveDecisionPolicy'
]
