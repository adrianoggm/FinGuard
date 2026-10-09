"""The engine's interpreter: one model call per turn, answered as typed operations.

The only part of the model-led engine that reads the person's sentence. It turns the sentence and
the canonical state into a prompt, holds the model to a closed schema, and reads the answer back as
the domain's operations; everything after that is pure code in `domain/engine`.

Self-contained on purpose: nothing here imports the rules engine's prompts, readers or planner
(spec: "Hard boundaries"), so what this package costs is what the model-led interpreter costs.
"""
