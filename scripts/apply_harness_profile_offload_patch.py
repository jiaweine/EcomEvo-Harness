from pathlib import Path

# Second push intentionally triggers the already-present one-shot workflow.


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, found {count}")
    target.write_text(text.replace(old, new, 1))


replace_once(
    "ecomevo/runtime/bundled_harness_optimizer.py",
    "                raise cancelled\n\n    def record_outcome(\n",
    "                raise cancelled\n\n"
    "    async def profile_async(self, domain: str, *, session_key: str) -> dict[str, Any]:\n"
    "        \"\"\"Run the built-in startup profile read off the asyncio event loop.\"\"\"\n"
    "        return await self._run_io(self.profile, domain, session_key=session_key)\n\n"
    "    def record_outcome(\n",
)

replace_once(
    "ecomevo/runtime/engine.py",
    "        harness_profile=self.harness.profile(goal.domain.value,session_key=sid)\n",
    "        profile_async=getattr(self.harness,'profile_async',None)\n"
    "        if sink is None and callable(profile_async):\n"
    "            harness_profile=await profile_async(goal.domain.value,session_key=sid)\n"
    "        else:\n"
    "            harness_profile=self.harness.profile(goal.domain.value,session_key=sid)\n",
)
