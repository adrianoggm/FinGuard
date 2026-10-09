"""The model-led engine's vertical slice: one turn, from operations to a saved state.

The executor runs a work through the banking ports, the presenter writes it with the existing
writers, and `answer_turn` chains interpret, ground, transition, execute, present and save.
"""
