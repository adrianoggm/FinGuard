from datetime import datetime
from typing import Protocol


class SessionClock(Protocol):
    def now(self) -> datetime: ...
