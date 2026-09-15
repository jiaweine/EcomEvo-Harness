from .engine import EcomEvoEngine
from .event_store import EventStore
from .planner import AdaptivePlanner
from .verifier import DecisionVerifier
from .sandbox import ActionSandbox
from .tools import ToolRegistry, PTCExecutor
from .hybrid_retrieval import install_hybrid_evidence_search
from .neural_rerank import install_neural_evidence_reranker
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

# Retrieval upgrades are read-only. Hybrid candidate selection and the optional
# neural reranker can improve evidence recall/ranking while Verifier/Governance/
# Action remain the only authority for business decisions and side effects.
install_hybrid_evidence_search()
install_neural_evidence_reranker()

__all__=[
    'EcomEvoEngine','EventStore','AdaptivePlanner','DecisionVerifier','ActionSandbox',
    'ToolRegistry','PTCExecutor','FailureDrivenEvolver','AutonomousController','TaskGraph',
    'AdaptiveSkillLibrary','AdaptiveDecisionPolicy','AdaptiveRoutingStore',
    'CounterfactualAdaptiveAutonomousController','CounterfactualAdaptiveDecisionPolicy',
    'HarnessEvolutionOptimizer','HarnessComponent','PluginRegistry','PluginDescriptor',
    'PluginContract','PluginError','PluginContractError','PluginLifecycleError'
]
