"""The answers the assistant gives about itself and about its own last answer.

Deterministic, and that is the point rather than a shortcut. "¿Quién eres?" has one true answer and
it is in this repository; "¿en qué categoría has clasificado el cine?" is answered by the previous
plan and the catalogue. Sending either to a model would be asking something that cannot know for
certain to guess at facts we hold.

Spanish, because it reaches the user.
"""

from finguard_orchestrator.domain.services import answer_writer, category_words
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

# What this assistant is and what it will do. Written once, here, so the answer cannot drift
# between a prompt, a README and a screen. It says what it *cannot* do too: an assistant that lists
# only its talents is one whose limits somebody discovers by being surprised.
IDENTITY = (
    "Soy FinGuard, tu asistente financiero. Esto es lo que puedo hacer:\n"
    "· Consultar tus movimientos y tus gastos por periodo, categoría o comercio.\n"
    "· Decirte tu saldo y qué se te ha ingresado.\n"
    "· Sacarte el gasto más alto de un periodo, y el segundo, y el tercero.\n"
    "· Calcular medias por movimiento, día, semana o mes.\n"
    "· Enseñarte en qué te gastas el dinero, agrupado por categoría, comercio, periodo o cuenta.\n"
    "· Comparar dos consultas consecutivas que solo cambien de periodo.\n"
    "· Preparar un bizum a uno de tus contactos, que tú tienes que confirmar antes de que salga.\n"
    "Lo que todavía no hago: transferencias a terceros, comparaciones con dos periodos nuevos "
    "en una sola frase, previsiones ni consejos de inversión.\n"
    "No invento números: cada cifra viene de tu banco y te enseño la consulta que he hecho."
)


def category_catalogue() -> str:
    """The supported categories, from the same vocabulary used to build and explain plans."""
    available = category_words.names()
    listed = ", ".join(available[:-1]) + f" y {available[-1]}"
    return (
        f"Puedo consultar estas categorías: {listed}. "
        "Puedes responder sólo con una de ellas cuando te pregunte por la categoría."
    )


def explain(sentence: str, plan: FinancialPlan | None) -> str:
    """Why the last answer said what it said.

    Two parts, and the second is the one that matters. Restating the plan is honest and often
    enough — somebody asking "¿qué consulta has hecho?" wants exactly that. But when the question
    names categories, what they are really asking is whether two things were filed together, and
    the catalogue answers that without the ledger being touched.
    """
    if plan is None:
        # Reachable when a client sends an explanation-shaped sentence with no plan behind it. The
        # router guards against it, and a second guard here costs nothing compared to explaining a
        # query that does not exist.
        return "Todavía no te he contestado nada, así que no hay ninguna consulta que explicar."

    said = f"La consulta que hice fue: {answer_writer.describe(plan)}."
    mentioned = category_words.named_in(sentence)
    if not mentioned:
        return said

    if len(mentioned) == 1:
        category = mentioned[0]
        placement = (
            f"«{category_words.name_of(category)}» es la categoría {category}"
            f"{_and_whether_used(category, plan)}."
        )
        return f"{said}\n{placement}"

    # Two or more named, which is almost always the real question: "¿los restaurantes son la misma
    # categoría que el cine?" The answer is the catalogue, and it is a no.
    listed = ", ".join(
        f"«{category_words.name_of(category)}» es {category}" for category in mentioned
    )
    return (
        f"{said}\n{listed}. Son categorías distintas, así que una consulta sobre una no incluye "
        f"la otra."
    )


def _and_whether_used(category: str, plan: FinancialPlan) -> str:
    """Whether that category was actually in the plan.

    This is the sentence that makes a misfiled question visible: somebody asks about the cinema,
    the plan says restaurants, and saying so plainly is what lets them correct it instead of
    believing the figure.
    """
    if category in plan.categories:
        return ", y es la que usé"
    if not plan.categories:
        return ", y no filtré por ninguna categoría"
    used = ", ".join(category_words.name_of(name) for name in plan.categories)
    return f", pero la consulta que hice filtraba por {used}"
