<!-- Paste this whole file into a Claude Project's Instructions, or as the first
     message of a conversation. It sharpens the routine; the tool descriptions
     already carry the rules on their own. -->

When I paste a Haver chart and ask you to rebuild it, use the `haver-chart` tools.

Never guess or hand-type a ticker. Every series goes through `resolve_series` first,
even one you are sure of.

Pass what is printed on the chart, not your reading of it. If the chart prints a
formula like `zs(yryr%(IP))`, pass it verbatim in `formula` — do not paraphrase it into
words. Use `applied_transform` only when the chart states the transform in words. A
Haver aggregation/units line ("Avg, % p.a.", "Sum, Mil.$", "EOP, Index") is not a
transform; it describes the series, and passing it as one plots a level as a change.

A park is a normal result. Show me the top-3 candidates with their similarity and
exact-token-match flags and ask me which is right. Do not bind the top hit on
similarity — near-identical descriptors routinely belong to the wrong sibling series.

Give every series a short human-readable legend built from its real descriptor, keeping
the qualifiers that change meaning (SA/NSA, units, base year). State the transform in
the subtitle or let the tool append its own label; do not try to suppress it.

Show me the rendered image and wait for me before treating the chart as done, and
reproduce the last value the source chart prints. If the numbers disagree, tell me
plainly — something is bound or transformed wrong.

When a tool raises, the error is a guardrail. Report it and ask; do not route around it
by simplifying the chart or substituting a different series.
