"""Shared typed tool descriptors. No application database dependencies."""
from dataclasses import dataclass
from typing import Callable
from uuid import UUID
from pydantic import BaseModel,ConfigDict


class Inputs(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class MutationInput(Inputs):
    # Strings deliberately accepted here: MCP JSON has no UUID scalar.
    operation_id:str


class ToolError(Exception):
    def __init__(self,message,code='operation_failed',*,uncertain=False):
        self.message,self.code,self.uncertain=message,code,uncertain
        super().__init__(message)


@dataclass(frozen=True)
class Tool:
    name:str
    description:str
    model:type[BaseModel]
    handler:Callable
    read_only:bool=True
    destructive:bool=False
    open_world:bool=False

    def descriptor(self):
        schema=self.model.model_json_schema()
        return {'name':self.name,'description':self.description,'inputSchema':schema,
                'annotations':{'readOnlyHint':self.read_only,'destructiveHint':self.destructive,
                               'idempotentHint':self.read_only,'openWorldHint':self.open_world}}
