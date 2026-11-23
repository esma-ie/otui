# otui: working rules for Claude

otui, the Open TIMES User Interface: a browser app for TIMES models in native
TIMES syntax (run files and DD files). Licence: AGPL-3.0-or-later.

- **No DD parser in otui.** Read models through TIMES.jl's reader
  (`compile_run`, `write_dump`) at a pinned commit, through one bridge
  module. Its Arrow dump (format v1) is otui's input format.
- The core does not depend on xl2times; it is an optional importer and a
  test oracle.
- DD files otui writes stay inside the subset TIMES.jl reads, so one `.dd`
  serves both GAMS TIMES and TIMES.jl.
- Table headers use TIMES code names in short form, as declared in the TIMES
  code (`NCAP_COST`: `REG, ALLYEAR, PRC, CUR, value`). Never Veda aliases.
- Test only on openly licensed models (TIMES_Demo `demo12` and its variants,
  `otcgen`, `otnyc`, `otus9r`). Fetch fixtures at a pinned commit with a
  SHA-256 check; never copy models or raw data into this repository.
- Lower-case file and folder names; follow Linux path rules.
- Table format: `docs/table-format.md`. Roadmap: `docs/roadmap.md`.
  Contributing: `CONTRIBUTING.md`.
