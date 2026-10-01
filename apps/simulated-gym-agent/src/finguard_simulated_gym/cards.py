from typing import Any

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    APIKeySecurityScheme,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)

from finguard_simulated_gym.config import Settings


def card_for(settings: Settings) -> AgentCard:
    norte = settings.style == "norte"
    name = "Gimnasio Norte" if norte else "Gimnasio Sur"
    if norte:
        description = "Gimnasio con horario de clases y reservas, respuestas estructuradas."
        skills = [
            AgentSkill(
                id="timetable",
                name="Horario de clases",
                description=(
                    "Consulta las clases disponibles por actividad y fecha, con las plazas libres."
                ),
                tags=["clases", "horario"],
                examples=["¿Qué clases de yoga hay el martes?"],
            ),
            AgentSkill(
                id="reserve",
                name="Reservar plaza",
                description="Reserva una plaza en una sesión concreta para un socio vinculado.",
                tags=["reserva"],
                examples=["Reserva la sesión N-1002-1900"],
            ),
        ]
        modes_in = ["text/plain", "application/json"]
        modes_out = ["application/json", "text/plain"]
        schemes = {
            "gymKey": SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(location="header", name="X-Gym-Key")
            )
        }
        requirements = [SecurityRequirement(schemes={"gymKey": StringList(list=[])})]
    else:
        description = "Gimnasio con clases y plazas, respuestas en texto."
        skills = [
            AgentSkill(
                id="classes",
                name="Clases del centro",
                description="Te digo qué clases hay y cuántas plazas quedan.",
                tags=["clases"],
            ),
            AgentSkill(
                id="book",
                name="Apuntarse a una clase",
                description="Apunta a un socio a una clase del centro.",
                tags=["reserva"],
            ),
        ]
        modes_in = ["text/plain"]
        modes_out = ["text/plain"]
        schemes = {
            "bearer": SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="Bearer")
            )
        }
        requirements = [SecurityRequirement(schemes={"bearer": StringList(list=[])})]
    return AgentCard(
        name=name,
        description=description,
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=f"{settings.a2a_url}/a2a/jsonrpc",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
            AgentInterface(
                url=f"{settings.a2a_url}/a2a/rest",
                protocol_binding="HTTP+JSON",
                protocol_version="1.0",
            ),
        ],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        provider=AgentProvider(organization=name, url=settings.public_url),
        default_input_modes=modes_in,
        default_output_modes=modes_out,
        skills=skills,
        security_schemes=schemes,
        security_requirements=requirements,
    )


def legacy_card(settings: Settings) -> dict[str, Any]:
    """A raw A2A 0.3 card: what a non-compliant agent serves. Not parseable as 1.0."""
    return {
        "name": "Gimnasio Antiguo",
        "description": "Gimnasio con un agente de la versión 0.3 del protocolo.",
        "url": f"{settings.a2a_url}/a2a",
        "preferredTransport": "JSONRPC",
        "protocolVersion": "0.3.0",
        "version": "0.9.0",
        "capabilities": {},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "classes",
                "name": "Clases",
                "description": "Clases del centro.",
                "tags": [],
            }
        ],
    }
