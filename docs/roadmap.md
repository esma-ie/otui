# otui roadmap

otui 0.1.0 is a preview: it reads a TIMES model's run file and DD files (with
TIMES.jl's reader), and lets you browse, check, solve and export it. What
comes next, in order:

1. **Spreadsheets as the source of truth.** Read the exported Excel and CSV
   tables back, write DD files from them that both GAMS TIMES and TIMES.jl
   read, and re-check the model when a file is saved.
2. **GAMS TIMES as a full backend**, from the same run, with its results read
   from the GDX file.
3. **Results dashboards**: scenarios compared against a reference, by sector,
   for emissions, system cost and capacity.
4. **Teaching**: a step-by-step path into TIMES for students and new
   modellers, built with TIMES.jl.
5. **A shared server**, with sign-in, a shared run queue and editing in the
   browser.

Throughout, otui checks GAMS TIMES and TIMES.jl against each other as
TIMES.jl's coverage grows.

Ideas and problems are welcome as issues.
