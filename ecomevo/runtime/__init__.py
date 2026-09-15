from .engine import EcomEvoEngine
from .event_store import EventStore
from .planner import AdaptivePlanner
from .verifier import DecisionVerifier
from .sandbox import ActionSandbox
from .tools import ToolRegistry, PTCExecutor
from .hybrid_retrieval import install_hybrid_evidence_search
from .evolver import FailureDrivenEvolver
from .autonomy import AutonomousController, TaskGraph
from .skills import AdaptiveSkillLibrary
from .adaptive_routing import AdaptiveDecisionPolicy, AdaptiveRoutingStore
from .counterfactual_routing import CounterfactualAdaptiveAutonomousController, CounterfactualAdaptiveDecisionPolicy
from .harness_evolution import HarnessEvolutionOptimizer, HarnessComponent
from .plugins import (
    PluginContract,
    PluginContractError,
    PluginDescriptor,
    PluginError,
    PluginLifecycleError,
    PluginRegistry,
)

# Evidence retrieval is read-only. Installing the hybrid layer upgrades candidate
# selection while leaving Verifier/Governance/Action authority unchanged.
install_hybrid_evidence_search()

__all__=[
    'EcomEvoEngine','EventStore','AdaptivePlanner','DecisionVerifier','ActionSandbox',
    'ToolRegistry','PTCExecutor','FailureDrivenEvolver','AutonomousController','TaskGraph',
    'AdaptiveSkillLibrary','AdaptiveDecisionPolicy','AdaptiveRoutingStore',
    'CounterfactualAdaptiveAutonomousController','CounterfactualAdaptiveDecisionPolicy',
    'HarnessEvolutionOptimizer','HarnessComponent','PluginRegistry','PluginDescriptor',
    'PluginContract','PluginError','PluginContractError','PluginLifecycleError'
]
