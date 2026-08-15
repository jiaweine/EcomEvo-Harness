from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class PluginDescriptor:
    key:str
    kind:str
    name:str
    version:str='1.0'
    enabled:bool=True
    description:str=''


class PluginRegistry:
    """Runtime plugin catalog backed by actual instances, not only labels."""
    def __init__(self):
        self._plugins:dict[str,PluginDescriptor]={}
        self._instances:dict[str,Any]={}

    def register(self,key:str,kind:str,name:str,description:str='',version:str='1.0',instance:Any=None):
        self._plugins[key]=PluginDescriptor(key,kind,name,version,True,description)
        if instance is not None:self._instances[key]=instance

    def get(self,key:str,default:Any=None)->Any:
        desc=self._plugins.get(key)
        if not desc or not desc.enabled:return default
        return self._instances.get(key,default)

    def replace(self,key:str,instance:Any,*,version:str|None=None):
        if key not in self._plugins:raise KeyError(key)
        self._instances[key]=instance
        if version:self._plugins[key].version=version

    def set_enabled(self,key:str,enabled:bool):
        if key not in self._plugins:raise KeyError(key)
        self._plugins[key].enabled=bool(enabled)

    def describe(self)->list[dict[str,Any]]:
        rows=[]
        for key,desc in self._plugins.items():
            row=asdict(desc);row['loaded']=key in self._instances;rows.append(row)
        return rows

    def by_kind(self,kind:str)->list[dict[str,Any]]:
        return [x for x in self.describe() if x['kind']==kind]
