"""What this service says about a turn, what it decided, and what it will never say.

Three modules, one rule. `logging` is the record a person greps; `decisions` is the record a
dashboard counts; `otel` is the one that ships the second one somewhere. They are split because
they are read by different people at different times, and joined because the thing none of them
may contain is the same thing: anything the holder wrote.

No re-exports: each name is imported from its own module, and `otel`, the only module that imports
OpenTelemetry, is imported only by the composition root.
"""
