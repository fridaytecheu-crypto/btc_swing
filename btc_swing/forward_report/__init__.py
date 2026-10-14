"""Generic READ-ONLY forward reporting layer shared by V10, V11 and V11-WARM.

It only reads journals, state and freezes; it never writes anywhere except `reports/<generation>/`,
never touches a runner, a counter, an observation_start, a freeze or an exchange. A generation
describes its artefacts with a `GenerationSpec`; fields a generation does not have are reported as
N/A, never inferred."""
