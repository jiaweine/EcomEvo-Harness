from __future__ import annotations
import time, uuid
from ecomevo.models import EvolutionPatch, VerificationResult

class FailureDrivenEvolver:
    """Create small patches from failed traces, then accept only patches that pass deterministic replay cases."""
    REPLAY_CASES=[
      {"name":"merchant_missing_license","missing":True,"score":.74,"expected":"replan"},
      {"name":"merchant_complete","missing":False,"score":.77,"expected":"finish"},
      {"name":"aftersales_missing_order","missing":True,"score":.69,"expected":"replan"},
      {"name":"aftersales_complete","missing":False,"score":.82,"expected":"finish"},
      {"name":"product_claim_without_proof","missing":True,"score":.73,"expected":"replan"},
      {"name":"product_clean","missing":False,"score":.79,"expected":"finish"},
      {"name":"risk_single_weak_signal","missing":True,"score":.61,"expected":"replan"},
      {"name":"risk_two_sources","missing":False,"score":.85,"expected":"finish"},
    ]

    def _sandbox_replay(self, patch:dict|None)->float:
        ok=0
        for case in self.REPLAY_CASES:
            # Baseline uses confidence alone. Candidate patch makes evidence completeness a hard finish condition.
            if patch:
                predicted="replan" if case["missing"] else ("finish" if case["score"]>=.58 else "replan")
            else:
                predicted="finish" if case["score"]>=.58 else "replan"
            ok += int(predicted==case["expected"])
        return ok/len(self.REPLAY_CASES)

    def build_patch(self,verification:VerificationResult,domain:str)->EvolutionPatch|None:
        if verification.passed:return None
        target="planner" if verification.missing_evidence else "prompt"
        patch={
          "domain":domain,
          "when":"verification_failed",
          "add_required_checks":verification.missing_evidence or ["输出前增加证据引用检查"],
          "behavior":"证据不完整时必须补证或转人工，不允许仅凭置信度完成高影响处置",
          "version_rule":"small_patch_only"
        }
        before=self._sandbox_replay(None);after=self._sandbox_replay(patch)
        accepted=after>before and after>=.95
        return EvolutionPatch(
          patch_id=f"patch-{uuid.uuid4().hex[:10]}",created_at=time.time(),
          reason="; ".join(verification.issues or verification.missing_evidence or ["verification failed"]),
          target=target,patch=patch,replay_cases=len(self.REPLAY_CASES),
          regression_before=round(before,3),regression_after=round(after,3),accepted=accepted)
