from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict


class ToolCallRequest(BaseModel):
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)


class PageElement(BaseModel):
    index: int = 0
    tag: str = ""
    text: str = ""
    selector: str = ""
    attributes: Dict[str, str] = Field(default_factory=dict)


class PageSnapshot(BaseModel):
    url: str = ""
    title: str = ""
    elements: List[PageElement] = Field(default_factory=list)
    visible_text: str = ""
    timestamp: str = ""


class AgentPlanResponse(BaseModel):
    thoughts: str = ""
    actions: List[ToolCallRequest] = Field(default_factory=list)
    done: bool = False
    final_answer: Optional[str] = None


class ToolResult(BaseModel):
    ok: bool
    result: Any = None
    error: Optional[str] = None

    def summary(self, limit: int = 300) -> str:
        if not self.ok:
            return f"ERROR: {self.error}"
        text = str(self.result)
        return text if len(text) <= limit else text[:limit] + "..."
