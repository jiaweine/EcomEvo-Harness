from __future__ import annotations
from collections import deque
from typing import Any

class RuntimeMemory:
    def __init__(self,max_items:int=300): self.items=deque(maxlen=max_items)
    def add(self,item:dict[str,Any]): self.items.append(item)
    def relevant(self,domain:str,limit:int=6):
        rows=[x for x in reversed(self.items) if x.get('domain')==domain]
        return rows[:limit]
