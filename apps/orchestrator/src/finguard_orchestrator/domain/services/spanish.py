"""Reading Spanish the way people type it.

Nobody types accents into a chat box, and a rule that needs them is a rule that does not fire.
From the reported conversation:

    "De esos gastos cuantos fueron en el supermercado ?"
    → "449,62 € el mes pasado, en 8 movimientos."

`COUNT_ANSWERED_AS_AMOUNT` exists to refuse exactly that, and it did not fire: its pattern was
`\\bcuánt[oa]s\\b` and the user wrote "cuantos". The answer was a total presented as an answer to
"how many" — a real number about a different question.

The capability guard's families survived only because whoever wrote them happened to spell the
alternatives out as `[áa]`. Relying on remembering that in every future pattern is a habit, not a
rule. So the sentence is folded once here and the patterns are written unaccented.

This existed three times already — in the category vocabulary, in the window vocabulary, and
privately in each — and neither of the two rule sets that most needed it had a copy.
"""

import unicodedata


def folded(text: str) -> str:
    """Lower case, no accents, ñ as n.

    `ñ → n` is deliberate and load-bearing for the window vocabulary: "este ano" is what somebody
    typing quickly writes, and it has to match "este año". The cost is that a word distinguished
    only by the tilde would collide, which in this vocabulary is nothing — there is no pair of
    categories, periods or rules where "n" and "ñ" mean different things.
    """
    stripped = unicodedata.normalize("NFD", text.casefold())
    return "".join(character for character in stripped if not unicodedata.combining(character))


def one_edit_apart(typed: str, word: str) -> bool:
    """Whether `typed` is `word` with at most one letter wrong.

    Substitution, insertion, deletion or a transposition of two neighbours — the four things a
    thumb does on a phone. Damerau-Levenshtein bounded at one, which needs no table: the strings
    differ in length by at most one, so a single scan to the first difference and a comparison of
    the remainders decides it.

    **For closed lexicons only.** Written for the words that route money: "bizum" is a word most
    people have never had to type, and it arrived as bixum, bisum and vizum in three different
    reported conversations. Enumerating the misspellings is enumerating an open set — every one that
    was missed made the whole payment path silently unreachable, which is the worst shape a defect
    can have. One edit against a three-word lexicon closes the class instead of the instance.

    Length four is the floor because below it a single edit reaches too far: at three letters almost
    everything is one edit from everything, and the caller's vocabulary would start matching prose.
    """
    if len(word) < 4:
        return typed == word
    if typed == word:
        return True
    if abs(len(typed) - len(word)) > 1:
        return False
    ahead = next((i for i, (a, b) in enumerate(zip(typed, word, strict=False)) if a != b), None)
    if ahead is None:
        # One is a prefix of the other, and their lengths differ by one at most.
        return True
    if len(typed) == len(word):
        # A substitution, or two neighbours swapped.
        rest = slice(ahead + 1, None)
        swapped = (
            typed[ahead] == word[ahead + 1 : ahead + 2]
            and typed[ahead + 1 : ahead + 2] == word[ahead : ahead + 1]
        )
        return typed[rest] == word[rest] or (swapped and typed[ahead + 2 :] == word[ahead + 2 :])
    longer, shorter = (typed, word) if len(typed) > len(word) else (word, typed)
    return longer[ahead + 1 :] == shorter[ahead:]
