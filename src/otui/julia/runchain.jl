# otui's run of a TIMES model on TIMES.jl, with structured results for the API.
#
# The steps are those of TIMES.jl's scripts/run_dd.jl (esma-ie/timesjl, GPL-3.0-or-later) at the
# pinned commit: compile the run file and DD files, write the dump, run TIMES.jl's preprocessing
# chain (GATE_SEGMENTS / gate_steps from scripts/m13_gate.jl), write TIMES's QA_CHECK.LOG, generate
# the model and solve it with HiGHS. Loaded by worker.jl after scripts/m13_gate.jl, so the chain is
# TIMES.jl's own, not a copy. A TIMES feature TIMES.jl does not translate yet stops the run with a
# "not translated" status naming the preprocessing segment.

# The steps of a run, in order. A run stops after `until` (a button per step in otui, each
# running the steps up to it; "Solve" runs them all).
#   read        compile the run file and DD files (TIMES.jl's reader), write the Arrow dump
#   preprocess  TIMES.jl's preprocessing chain (GATE_SEGMENTS), TIMES's QA_CHECK.LOG
#   build       generate the model (JuMP)
#   pass        hand it to the solver: JuMP copies the model into the solver's memory
#   solve       the solver's own run
#   results     read the solution back: objective, every variable's value, dummy imports
const OTUI_STEPS = ("read", "preprocess", "build", "pass", "solve", "results")

"""
    otui_solve(run, dirs, out, globals; until="results", solver="highs", threads=12, progress=identity)

Run a TIMES model on TIMES.jl, step by step (OTUI_STEPS). `progress` is called with a Dict at
the start and end of each step (`step`, `state` = running/done/failed, `seconds`, `log`), so
otui can show timers and logs while the run goes on. `solver` is "highs" or "cuopt" (cuOpt.jl
must be loadable: the WSL worker).
"""
function otui_solve(run::String, dirs::Vector{String}, out::String, globals::Dict{String,String};
                    until::String="results", solver::String="highs", threads::Int=12,
                    progress=identity, solve::Bool=true)
    solve || (until = "build")
    until in OTUI_STEPS || error("unknown step $until; one of $(join(OTUI_STEPS, ", "))")
    name = splitext(basename(run))[1]
    res = Dict{String,Any}("model" => name, "until" => until, "solver" => solver)
    steps = Dict{String,Any}[]
    res["steps"] = steps
    tm = nothing
    m = nothing

    # one step: timed, reported before and after; its function returns log lines
    function step!(f, key)
        progress(Dict{String,Any}("step" => key, "state" => "running"))
        t0 = time()
        log = String[]
        try
            log = f()
        catch e
            dt = time() - t0
            msg = first(sprint(showerror, e), 4000)
            push!(steps, Dict{String,Any}("step" => key, "state" => "failed", "seconds" => dt, "log" => [msg]))
            progress(steps[end])
            rethrow()
        end
        dt = time() - t0
        push!(steps, Dict{String,Any}("step" => key, "state" => "done", "seconds" => dt, "log" => log))
        progress(steps[end])
        return key == until
    end

    g = nothing
    step!("read") do
        g = TIMES.compile_run(solve_now_no(run); dirs, globals)
        TIMES.write_dump(g, out; source="$name: compiled from its DD files by TIMES.jl")
        res["compile_warnings"] = g.warnings
        ["$(basename(run)) and its DD files read by TIMES.jl $(pkgversion(TIMES))",
         "$(length(readdir(out))) dump files written; $(g.warnings) compile warnings"]
    end && return finish!(res, "read")

    sw = run_switches(run)
    try
        step!("preprocess") do
            tm = TimesModel(out; switches=sw, reduce=uppercase(get(sw, "REDUCE", "NO")) == "YES")
            qa_warnings!(tm, g.warnings)
            for (a, b) in GATE_SEGMENTS
                res["segment"] = "$a -> $b"
                foreach(f -> f(tm), gate_steps(a))
            end
            delete!(res, "segment")
            qa = qa_text(tm)
            write(joinpath(out, "QA_CHECK.LOG"), qa)
            res["qa_log"] = qa
            nqa = count(l -> occursin(r"\*\d+ (WARNING|ERROR|SEVERE|FATAL)"i, l), split(qa, "\n"))
            ["$(length(GATE_SEGMENTS)) preprocessing segments (TIMES.jl's chain, as run_dd.jl)",
             "QA_CHECK.LOG: $nqa entries",
             "switches: " * join(("$k=$v" for (k, v) in sort!(collect(sw))), ", ")]
        end && return finish!(res, "preprocess")
    catch e
        e isa TIMES.UnportedError || rethrow()
        res["status"] = "not_translated"
        res["message"] = first(sprint(showerror, e), 4000)
        return res
    end

    # model generation can meet a TIMES feature TIMES.jl does not translate yet, too
    try
        step!("build") do
            generate!(tm)
            m = tm.model
            res["rows"] = JuMP.num_constraints(m; count_variable_in_set_constraints=false)
            res["columns"] = JuMP.num_variables(m)
            ["model generated: $(res["rows"]) rows x $(res["columns"]) columns"]
        end && return finish!(res, "build")
    catch e
        e isa TIMES.UnportedError || rethrow()
        res["status"] = "not_translated"
        res["segment"] = "model generation"
        res["message"] = first(sprint(showerror, e), 4000)
        return res
    end

    logfile = joinpath(out, "solver.log")
    step!("pass") do
        otui_optimizer!(m, solver, threads, logfile)
        JuMP.MOI.Utilities.attach_optimizer(m)  # the copy into the solver's memory
        res["solver_name"] = JuMP.solver_name(m)
        ["$(res["solver_name"]): model copied into the solver ($threads threads)"]
    end && return finish!(res, "pass", m, solver)

    st = nothing
    step!("solve") do
        JuMP.optimize!(m)
        st = JuMP.termination_status(m)
        res["termination"] = string(st)
        res["solve_seconds"] = JuMP.solve_time(m)
        lines = isfile(logfile) ? readlines(logfile) : String[]
        ["termination: $st", "solver time: $(round(res["solve_seconds"]; digits=3)) s",
         (length(lines) > 80 ? ["... ($(length(lines) - 80) earlier lines in solver.log)"; lines[end-79:end]] : lines)...]
    end && return finish!(res, "solve", m, solver)

    step!("results") do
        if st == JuMP.OPTIMAL
            res["objective"] = JuMP.objective_value(m)
            vals = JuMP.value.(JuMP.all_variables(m))
            res["nonzero_values"] = count(!iszero, vals)
            res["dummy_flows"] = otui_dummy_flows(tm)
            log = ["objective: $(res["objective"])",
                   "$(length(vals)) values read back, $(res["nonzero_values"]) non-zero",
                   "dummy import records: $(length(res["dummy_flows"]))"]
            # otui's Results tab: capacity and flows, as TIMES's report computes them (otui_results)
            try
                t = @elapsed n = otui_results(tm, joinpath(out, "results"))
                res["results"] = n
                push!(log, "results: " * join(("$k $v" for (k, v) in sort!(collect(n))), ", ") *
                           " records ($(round(t; digits=1)) s)")
            catch e
                res["results_error"] = first(sprint(showerror, e), 2000)
                push!(log, "results not written: " * res["results_error"])
            end
            # cuOpt 26.10 (through cuOpt.jl 0.5.0) hands back barrier's solution, not the vertex
            # crossover reaches (demo12: 23,960,719.13 against the vertex's 23,960,685.74 in
            # cuOpt's own log; esma-ie/timesjl#100). Say so when the two differ.
            x = solver == "cuopt" ? otui_crossover_objective(logfile) : nothing
            if x !== nothing && abs(x - res["objective"]) > 1e-9 * max(1.0, abs(x))
                res["crossover_objective"] = x
                push!(log, "cuOpt's log: crossover reached objective $x; cuOpt returned " *
                           "barrier's solution ($(res["objective"]), relative difference " *
                           "$(round(abs(x - res["objective"]) / abs(x); sigdigits=2)))")
            end
            log
        else
            ["no solution to read: $(st)"]
        end
    end
    return finish!(res, "results", m, solver)
end

# the objective cuOpt reports after crossover, from its log ("Starting crossover" ... "Objective +x")
function otui_crossover_objective(logfile)
    isfile(logfile) || return nothing
    lines = readlines(logfile)
    i = findlast(l -> occursin("Starting crossover", l), lines)
    i === nothing && return nothing
    found = nothing
    for l in lines[i:end]
        m = match(r"^Objective\s+([-+]?[\d.]+(?:[eE][-+]?\d+)?)\s*$", strip(l))
        m === nothing || (found = parse(Float64, m.captures[1]))
    end
    return found
end

# the run's status once it stops (after `last`), and the step times under their older names.
# Last of all, HiGHS closes its log file (Windows keeps an open file locked); changing the
# setting earlier would make JuMP drop the solution.
function finish!(res, last, m=nothing, solver="")
    if m !== nothing && solver == "highs"
        try
            JuMP.set_attribute(m, "log_file", "")
        catch
        end
    end
    secs = Dict(s["step"] => s["seconds"] for s in res["steps"])
    for (k, n) in (("read", "compile_seconds"), ("preprocess", "preprocess_seconds"), ("build", "generate_seconds"))
        haskey(secs, k) && (res[n] = secs[k])
    end
    if last == "results" || last == "solve"
        st = get(res, "termination", "")
        res["status"] = st == "OPTIMAL" ? "optimal" : "not_optimal"
    else
        res["status"] = last == "build" || last == "pass" ? "generated" : "stopped"
    end
    res["stopped_after"] = last
    return res
end

# HiGHS (default) or cuOpt, with the run limits; HiGHS writes its log to `logfile`
function otui_optimizer!(m, solver, threads, logfile)
    if solver == "highs"
        JuMP.set_optimizer(m, HiGHS.Optimizer)
        JuMP.set_attribute(m, "threads", threads)
        JuMP.set_attribute(m, "log_to_console", false)
        JuMP.set_attribute(m, "output_flag", true)
        JuMP.set_attribute(m, "log_file", logfile)
    elseif solver == "cuopt"
        # cuOpt.jl is loaded only in the WSL worker that has it (bridge.cuopt_command); barrier
        # with crossover, as TIMES.jl's cuOpt lane gates on (scripts/cuopt/README.md)
        Base.invokelatest(isdefined, Main, :cuOpt) || Base.eval(Main, :(using cuOpt))
        cu = Base.invokelatest(getglobal, Main, :cuOpt)  # loaded just now: the newest world
        Base.invokelatest() do
            JuMP.set_optimizer(m, cu.Optimizer)
            JuMP.set_attribute(m, cu.CUOPT_METHOD, cu.CUOPT_METHOD_BARRIER)
            JuMP.set_attribute(m, cu.CUOPT_CROSSOVER, true)
            JuMP.set_attribute(m, cu.CUOPT_NUM_CPU_THREADS, threads)
            try
                JuMP.set_attribute(m, cu.CUOPT_LOG_FILE, logfile)
            catch  # older cuOpt.jl: no log file parameter
            end
        end
    else
        error("unknown solver $solver: highs or cuopt (CPLEX runs through GAMS)")
    end
end

# otui's results: four report attributes VEDA users know, computed from TIMES.jl's solution as GAMS
# TIMES's rptmain.rpt (TIMES 4.9.7) computes them, until TIMES.jl translates the report itself (its
# M1.6). Written as Arrow files in `dir`, one record per region, period, process (and commodity and
# timeslice), summed over vintages:
#   VAR_Ncap(region, period, process)                       PAR_NCAPL = VAR_NCAP.L          (L160)
#   VAR_Cap(region, period, process)                        PAR_CAPL + PAR_PASTI            (L153-154)
#   VAR_FIn(region, period, process, commodity, timeslice)  F_IN: TOP IN flows, exports,
#                                                           storage input                   (L190-203)
#   VAR_FOut(...)                                           F_OUT: TOP OUT flows, imports,
#                                                           storage output                  (L191-204)
# A flow is the flow term of TIMES's equations (cal_nored!: reduced flows and emission factors
# included). Not included yet: the capacity-related flows (NCAP_COM: F_INOUT) and the auxiliary
# flows of trade and storage processes (F_INOUTS).
function otui_results(tm, dir)
    T = TIMES
    mkpath(dir)
    fam(n) = T.has_family(tm.vars, n) ? T.varfamily(tm.vars, n) : nothing
    keep(x) = abs(x) > 1e-9
    add!(d, k, x) = (d[k] = get(d, k, 0.0) + x)
    milestone = Set(k[1] for k in T.gset_opt(tm.data, "MILESTONYR", 1).keys)
    one = T.GNum(1)

    ncap = Dict{NTuple{3,String},Float64}()
    f = fam("VAR_NCAP")
    if f !== nothing
        for (k, ref) in f.refs
            k[2] in milestone || continue
            x = JuMP.value(ref)
            keep(x) && (ncap[(k[1], k[2], k[3])] = x)
        end
    end

    cap = Dict{NTuple{3,String},Float64}()
    pasti = T.gparam_opt(tm.data, "NCAP_PASTI", 3).values
    for ((r, v, t, p), c) in T.gparam_opt(tm.data, "COEF_CPT", 4).values
        x = get(ncap, (r, v, p), 0.0) + get(pasti, (r, v, p), 0.0)
        iszero(x) || add!(cap, (r, t, p), c * x)
    end
    filter!(kv -> keep(kv[2]), cap)

    fin = Dict{NTuple{5,String},Float64}()
    fout = Dict{NTuple{5,String},Float64}()
    tops = Dict{NTuple{3,String},Vector{String}}()
    for (r, p, c, io) in T.gset_opt(tm.data, "TOP", 4).keys
        push!(get!(tops, (r, p, c), String[]), io)
    end
    vint = Dict{NTuple{3,String},Vector{String}}()
    for (r, v, t, p) in T.gset_opt(tm.data, "RTP_VINTYR", 4).keys
        push!(get!(vint, (r, t, p), String[]), v)
    end
    emis = T.gset_opt(tm.data, "RPC_EMIS", 3).members
    flo = fam("VAR_FLO")
    for (r, t, p, c, s) in T.gset_opt(tm.data, "RTPCS_VARF", 5).keys
        ios = get(tops, (r, p, c), nothing)
        ios === nothing && continue
        x = 0.0
        for v in get(vint, (r, t, p), String[])
            if tm.reduce || (r, p, c) in emis  # substituted or emission flows: TIMES's flow term
                x += JuMP.value(T.cal_nored!(JuMP.AffExpr(0.0), tm, one, r, v, t, p, c, s))
            elseif flo !== nothing
                ref = get(flo.refs, (r, v, t, p, c, s), nothing)
                ref === nothing || (x += JuMP.value(ref))
            end
        end
        keep(x) || continue
        for io in ios
            io == "IN" ? add!(fin, (r, t, p, c, s), x) : io == "OUT" ? add!(fout, (r, t, p, c, s), x) : nothing
        end
    end
    f = fam("VAR_IRE")  # trade: exports go in, imports come out
    if f !== nothing
        for (k, ref) in f.refs
            x = JuMP.value(ref)
            keep(x) && add!(k[7] == "EXP" ? fin : fout, (k[1], k[3], k[4], k[5], k[6]), x)
        end
    end
    # ... and where REDUCE replaced VAR_IRE by the activity (RPC_AIRE: eqcombal.mod's
    # VAR_ACT * PRC_ACTFLO), from the activity
    aire = T.gset_opt(tm.data, "RPC_AIRE", 3).keys
    act = fam("VAR_ACT")
    if !isempty(aire) && act !== nothing
        ie = Dict{NTuple{3,String},Vector{String}}()
        for (r, p, c, x) in T.gset_opt(tm.data, "RPC_IRE", 4).keys
            push!(get!(ie, (r, p, c), String[]), x)
        end
        byp = Dict{NTuple{2,String},Vector{String}}()
        for (r, p, c) in aire
            push!(get!(byp, (r, p), String[]), c)
        end
        actflo = T.gparam_opt(tm.data, "PRC_ACTFLO", 4).values
        for (k, ref) in act.refs  # VAR_ACT(r, v, t, p, s)
            cs = get(byp, (k[1], k[4]), nothing)
            cs === nothing && continue
            a = JuMP.value(ref)
            keep(a) || continue
            for c in cs, x in get(ie, (k[1], k[4], c), String[])
                add!(x == "EXP" ? fin : fout, (k[1], k[3], k[4], c, k[5]),
                     a * get(actflo, (k[1], k[2], k[4], c), 1.0))
            end
        end
    end
    # storage: its own flows in and out replace the process flows (rptmain.rpt L203-204)
    eff = T.gparam_opt(tm.data, "STG_EFF", 3).values
    for (n, d, scale) in (("VAR_SIN", fin, false), ("VAR_SOUT", fout, true))
        f = fam(n)
        f === nothing && continue
        sums = Dict{NTuple{5,String},Float64}()
        for (k, ref) in f.refs
            x = JuMP.value(ref) * (scale ? get(eff, (k[1], k[2], k[4]), 1.0) : 1.0)
            keep(x) && add!(sums, (k[1], k[3], k[4], k[5], k[6]), x)
        end
        merge!(d, sums)
    end

    function save(name, d, cols)
        ks = sort!(collect(keys(d)))
        cs = [Symbol(c) => String[k[i] for k in ks] for (i, c) in enumerate(cols)]
        T.Arrow.write(joinpath(dir, name * ".arrow"), (; cs..., value = Float64[d[k] for k in ks]))
        return length(ks)
    end
    rtp = ("region", "period", "process")
    flow = ("region", "period", "process", "commodity", "timeslice")
    return Dict{String,Int}("VAR_Ncap" => save("VAR_Ncap", ncap, rtp), "VAR_Cap" => save("VAR_Cap", cap, rtp),
                            "VAR_FIn" => save("VAR_FIn", fin, flow), "VAR_FOut" => save("VAR_FOut", fout, flow))
end

# VEDA's dummy imports (IMPNRGZ, IMPMATZ, IMPDEMZ, ...): processes that can supply any commodity of
# their type at a high cost, to keep a model feasible. Their import flows in the solution, summed
# over vintages and timeslices: one record per region, period, process and commodity, with the
# annual flow (commodity units a year) and the period's length D(T) in years.
const OTUI_DUMMY = r"^IMP[A-Z]{3}Z$"

function otui_dummy_flows(tm)
    D = TIMES.gparam(tm, "D")
    acc = Dict{NTuple{4,String},Float64}()
    # VAR_IRE(R,V,T,P,C,S,IE) for trade-style dummies, VAR_FLO(R,V,T,P,C,S) for TOP-style ones
    for (fam, keep) in (("VAR_IRE", k -> k[7] == "IMP"), ("VAR_FLO", k -> true))
        TIMES.has_family(tm.vars, fam) || continue
        f = TIMES.varfamily(tm.vars, fam)
        for k in f.keys
            (occursin(OTUI_DUMMY, k[4]) && keep(k)) || continue
            x = JuMP.value(f.refs[k])
            abs(x) > 1e-9 || continue
            key = (k[1], k[3], k[4], k[5])
            acc[key] = get(acc, key, 0.0) + x
        end
    end
    return [Dict{String,Any}("region" => r, "period" => t, "process" => p, "commodity" => c,
                             "annual" => v, "years" => TIMES.gv(D, (t,)))
            for ((r, t, p, c), v) in sort!(collect(acc); by=first)]
end
