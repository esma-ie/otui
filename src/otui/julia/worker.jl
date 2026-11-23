# otui's Julia worker: a long-lived process that serves TIMES.jl to otui.
#
# Protocol: one JSON request per line on stdin, {"id": n, "cmd": "...", ...}.
# Each reply is one line on stdout starting with "@@OTUI " followed by JSON,
# {"id": n, "ok": true|false, ...}. Anything else on stdout or stderr is log
# output (packages may print), and otui keeps it as such. The first reply,
# with id 0, says the worker is ready.
#
# Commands:
#   ping                               -> {}
#   info                               -> TIMES.jl and Julia versions, threads
#   demo_dir                           -> {"dir": the pinned TIMES_Demo model folder}
#   compile {run, dirs?, out, globals?} -> {"out", "symbols", "seconds"}
#       compiles a TIMES run file and its DD files with TIMES.jl's reader
#       (compile_run) and writes the result as an Arrow dump (write_dump,
#       format version 1: manifest.json, uels.arrow, <symbol>.arrow).
#   solve {run, dirs?, out, globals?, until?, solver?}
#                                      -> TIMES.jl's run chain (runchain.jl), step by step up to
#       `until` (read, preprocess, build, pass, solve, results), with HiGHS or cuOpt: status,
#       objective, QA log, rows, columns, and each step's time and log. While it runs, the worker
#       sends {"id": n, "event": "progress", "step", "state", "seconds"?, "log"?} lines.
#   quit                               -> stops the worker

using JSON
using TIMES

# The cuOpt worker (in WSL: otui.bridge.cuopt_command) loads cuOpt.jl now, before serving. Loaded
# mid-request instead, its methods would be too new for the run already going (Julia's world age).
if get(ENV, "OTUI_SOLVER", "") == "cuopt"
    using cuOpt
end

const PREFIX = "@@OTUI "
const RUNCHAIN = Ref(false)

# TIMES.jl's run chain lives in its scripts (scripts/m13_gate.jl, as used by run_dd.jl); load it,
# and otui's runchain.jl, on the first solve. Functions defined by `include` at run time are
# called with invokelatest.
function load_runchain!()
    RUNCHAIN[] && return
    Base.include(Main, joinpath(pkgdir(TIMES), "scripts", "m13_gate.jl"))
    Base.include(Main, joinpath(@__DIR__, "runchain.jl"))
    RUNCHAIN[] = true
end
const OUT = stdout

function reply(d::AbstractDict)
    println(OUT, PREFIX, JSON.json(d))
    flush(OUT)
end

errtext(e, bt) = first(sprint(showerror, e, bt), 4000)

# As TIMES.jl's scripts/run_dd.jl does: a copy of the run file with SOLVE_NOW NO just before
# maindrv.mod, so GAMS's solve and report files are not compiled. The original is untouched; the
# copy goes to a temporary folder, and the DD files are found through `dirs`.
function solve_now_no(run::AbstractString)
    lines = readlines(run)
    i = findlast(l -> occursin(r"^\s*\$\s*batinclude\s+maindrv\.mod"i, l), lines)
    i === nothing && error("$(basename(run)) has no \$BATINCLUDE maindrv.mod line: is it a TIMES run file?")
    insert!(lines, i, "\$SET SOLVE_NOW NO")
    run2 = joinpath(mktempdir(), basename(run))
    write(run2, join(lines, "\n") * "\n")
    return run2
end

function handle(cmd::AbstractString, req, id=nothing)
    if cmd == "ping"
        return Dict{String,Any}()
    elseif cmd == "info"
        return Dict{String,Any}(
            "julia" => string(VERSION),
            "times_jl" => string(pkgversion(TIMES)),
            "times_gams_version" => string(TIMES.TIMES_GAMS_VERSION),
            "times_gams_commit" => TIMES.TIMES_GAMS_COMMIT,
            "times_demo_commit" => TIMES.TIMES_DEMO_COMMIT,
            "threads" => Threads.nthreads(),
        )
    elseif cmd == "demo_dir"
        return Dict{String,Any}("dir" => TIMES.times_demo_dir())
    elseif cmd == "compile"
        run = String(req["run"])
        dirs = String[String(d) for d in get(req, "dirs", String[])]
        isempty(dirs) && push!(dirs, dirname(run))
        out = String(req["out"])
        name = splitext(basename(run))[1]
        globals = Dict{String,String}(String(k) => String(v) for (k, v) in get(req, "globals", Dict()))
        get!(globals, "RUN_NAME", name)
        t = @elapsed begin
            g = TIMES.compile_run(solve_now_no(run); dirs, globals)
            TIMES.write_dump(g, out; source="$name: compiled from its DD files by TIMES.jl")
        end
        manifest = JSON.parsefile(joinpath(out, "manifest.json"))
        nsym = manifest isa AbstractDict && haskey(manifest, "symbols") ? length(manifest["symbols"]) :
               manifest isa AbstractVector ? length(manifest) : -1
        return Dict{String,Any}("out" => out, "symbols" => nsym, "seconds" => t)
    elseif cmd == "solve"
        run = String(req["run"])
        dirs = String[String(d) for d in get(req, "dirs", String[])]
        isempty(dirs) && push!(dirs, dirname(run))
        globals = Dict{String,String}(String(k) => String(v) for (k, v) in get(req, "globals", Dict()))
        get!(globals, "RUN_NAME", splitext(basename(run))[1])
        load_runchain!()
        threads = parse(Int, get(ENV, "TIMESJL_THREADS", "12"))
        otui_solve = Base.invokelatest(getglobal, Main, :otui_solve)
        progress = d -> reply(merge(d, Dict{String,Any}("id" => id, "event" => "progress")))
        until = String(get(req, "until", Bool(get(req, "solve", true)) ? "results" : "build"))
        return Base.invokelatest(otui_solve, run, dirs, String(req["out"]), globals;
                                 until, solver=String(get(req, "solver", "highs")), threads, progress)
    else
        error("unknown command: $cmd")
    end
end

function main()
    # the process id, so otui can end the worker mid-solve (Kill the job; in WSL, from Windows)
    reply(Dict("id" => 0, "ok" => true, "event" => "ready", "pid" => getpid()))
    for line in eachline(stdin)
        isempty(strip(line)) && continue
        id = nothing
        try
            req = JSON.parse(line)
            id = get(req, "id", nothing)
            cmd = String(req["cmd"])
            if cmd == "quit"
                reply(Dict("id" => id, "ok" => true))
                break
            end
            res = handle(cmd, req, id)
            res["id"] = id
            res["ok"] = true
            reply(res)
        catch e
            reply(Dict("id" => id, "ok" => false, "error" => errtext(e, catch_backtrace())))
        end
    end
end

main()
