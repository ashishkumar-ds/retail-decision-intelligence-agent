# Numbers are redacted before text leaves the process

Any text sent to a third-party service (the classifier.dev pre-filter and
root-cause analytics) is stripped of figures, percentages, amounts and store
ids first. The classifier needs semantics, not magnitudes; a dropped number
costs nothing, while an egressed business figure is a data-leak boundary the
system cannot walk back. Redaction is also why board tags carry visible
per-store confidence: without magnitudes the model is legitimately less sure
on fuzzy causes, and that uncertainty is shown rather than hidden.
