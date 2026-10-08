"""The model-led conversation engine's pure core: state, operations, grounding, transitions.

A sibling of the rules engine, not a layer over it. A model turns each turn into typed operations;
everything in this package then works on those operations alone. Nothing here reads the person's
sentence, which is what keeps it from growing into another linguistic reader, and nothing here
imports one (spec: "Hard boundaries").
"""
