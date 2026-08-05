import asyncio
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class JobState:
    job_id: str
    filename: str
    status: Literal["queued", "uploading", "extracting", "done", "error"] = "queued"
    progress_messages: list[str] = field(default_factory=list)
    result_zip: bytes | None = None
    error: str | None = None
    
    # Event to signal clients waiting for new progress
    _new_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def add_message(self, msg: str):
        self.progress_messages.append(msg)
        self._new_event.set()
        
    def wait_for_new_event(self) -> asyncio.Event:
        self._new_event.clear()
        return self._new_event

# Global in-memory job store
jobs: dict[str, JobState] = {}
