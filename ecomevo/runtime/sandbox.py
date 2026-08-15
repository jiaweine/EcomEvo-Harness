from __future__ import annotations
from dataclasses import dataclass

@dataclass
class SandboxDecision:
    allowed: bool
    reason: str
    requires_confirmation: bool=False

class ActionSandbox:
    READ_ONLY={'media.summarize','evidence.search','policy.lookup','catalog.inspect','merchant.inspect','order.inspect','risk.scan'}
    SIDE_EFFECT={'listing.disable','merchant.hold','merchant.approve','refund.issue','case.close'}
    def validate_tool(self,name:str)->SandboxDecision:
        if name in self.READ_ONLY:return SandboxDecision(True,'read-only')
        if name in self.SIDE_EFFECT:return SandboxDecision(False,'side-effect requires explicit confirmation',True)
        if name.startswith('mcp.'):return SandboxDecision(True,'remote tool allowed by configured MCP connection')
        return SandboxDecision(False,'tool is not allowlisted')
    def validate_action(self,side_effect:bool,confirmed:bool=False)->SandboxDecision:
        if not side_effect:return SandboxDecision(True,'no side effect')
        if confirmed:return SandboxDecision(True,'confirmed by operator')
        return SandboxDecision(False,'operator confirmation required',True)
