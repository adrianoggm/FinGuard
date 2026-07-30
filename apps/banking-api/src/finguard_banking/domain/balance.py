from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Balance:
    """Saldo de una cuenta en un instante concreto.

    `booked` es lo que dice el libro mayor. `available` es lo que el cliente puede
    gastar: hoy coinciden, y dejarán de coincidir cuando existan importes
    reservados por operaciones pendientes de confirmar (Fase 5).
    """

    account_id: UUID
    available_minor: int
    booked_minor: int
    currency: str
    as_of: datetime
