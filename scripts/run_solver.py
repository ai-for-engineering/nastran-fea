"""
Invoke the MYSTRAN solver on a Nastran BDF deck and verify the run actually
succeeded, instead of trusting the process exit code.

This wraps the manual workflow described in the README:

    cp models/lug_model.bdf models/lug_model.dat
    ./solver/mystran-19.0.0-windows-x86_64.exe models/lug_model.dat

Two things learned by testing MYSTRAN 19.0.0 directly (see PR description /
commit message for the exact commands) drive the design here:

1. MYSTRAN's process exit code is 0 whether the solve succeeded or fatally
   errored (e.g. an unconstrained model, or a deck missing ENDDATA both
   exited 0). "MYSTRAN terminated normally" on stdout is likewise not
   trustworthy on its own -- fatal errors can be logged earlier in the run
   and the process still reaches a normal-looking termination. The only
   reliable signal is scanning the .F06 file for "*ERROR" / "FATAL" markers.
   MYSTRAN's stdout usually also prints a cheap early hint when this
   happens: "CHECK F06 OUTPUT FILE FOR N FATAL MESSAGE(S)".

2. If the input file has no extension at all, MYSTRAN appends ".DAT" itself;
   if *that* file doesn't exist either, it blocks on stdin waiting for a
   filename ("hit Ctrl-Break to start over") instead of failing cleanly --
   which would hang an automated caller forever. Any *explicit* extension
   (.bdf, .dat, ...) is read as given. To stay unambiguous and to never
   mutate the caller's original BDF, this script always stages a sibling
   copy with a ".dat" extension and always redirects stdin from the null
   device as a backstop against the hang.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BDF_PATH = REPO_ROOT / "models" / "lug_model.bdf"
DEFAULT_SOLVER_PATH = REPO_ROOT / "solver" / "mystran-19.0.0-windows-x86_64.exe"
DEFAULT_TIMEOUT_S = 600

# Fatal-error markers observed in real MYSTRAN .F06 output (verified against
# both a clean solve and deliberately broken decks -- see commit message):
#   " *ERROR  1006: NO SPC ENTRY WAS FOUND IN BULK DATA DECK ..."
#   " *ERROR  1011: NO    ENDDATA ENTRY FOUND BEFORE END OF FILE ..."
# The asterisk must immediately precede the word so that benign lines like
# "...EPSILON ERROR ESTIMATE = ..." (printed on every successful run) don't
# false-positive.
F06_ERROR_PATTERNS = [
    re.compile(r"\*\s*ERROR", re.IGNORECASE),
    re.compile(r"\bFATAL\b", re.IGNORECASE),
    re.compile(r"\*\s*ABORT", re.IGNORECASE),
]

# Cheap, strong early signal from MYSTRAN's own stdout, e.g.:
#   "CHECK F06 OUTPUT FILE FOR        1 FATAL MESSAGE(S)"
STDOUT_FATAL_HINT = re.compile(r"FATAL MESSAGE", re.IGNORECASE)


@dataclass
class SolverResult:
    """Outcome of a single MYSTRAN invocation."""

    success: bool
    dat_path: Path
    f06_path: Path
    op2_path: Path
    returncode: int
    errors: list[str] = field(default_factory=list)
    stdout: str = ""


def _stage_dat_file(bdf_path: Path) -> Path:
    """Copy bdf_path to a sibling file with a .dat extension (MYSTRAN's
    expected input extension) and return that path. Leaves the original BDF
    untouched. If bdf_path already ends in .dat, no copy is made."""
    dat_path = bdf_path.with_suffix(".dat")
    if dat_path != bdf_path:
        shutil.copyfile(bdf_path, dat_path)
    return dat_path


def _scan_f06_for_errors(f06_path: Path) -> list[str]:
    """Return a list of 'filename:line: text' strings for every F06 line
    that matches a fatal-error pattern."""
    found = []
    with f06_path.open("r", errors="replace") as f:
        for lineno, line in enumerate(f, start=1):
            if any(pattern.search(line) for pattern in F06_ERROR_PATTERNS):
                found.append(f"{f06_path.name}:{lineno}: {line.strip()}")
    return found


def run_solver(
    bdf_path: str | Path = DEFAULT_BDF_PATH,
    solver_exe_path: str | Path = DEFAULT_SOLVER_PATH,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> SolverResult:
    """Run the MYSTRAN solver on `bdf_path` and verify the result by parsing
    the .F06 output for fatal errors.

    Raises RuntimeError for infrastructure problems that prevent the solve
    from running at all (missing input file, missing solver binary, or the
    solver process not finishing within `timeout`). A solve that *runs* but
    fails on its own terms (fatal errors in the F06) is reported via
    SolverResult(success=False, errors=[...]) rather than an exception, so
    programmatic callers (e.g. a future MCP tool) can inspect the failure
    without a try/except.
    """
    bdf_path = Path(bdf_path).resolve()
    solver_exe_path = Path(solver_exe_path).resolve()

    if not bdf_path.is_file():
        raise RuntimeError(f"BDF file not found: {bdf_path}")
    if not solver_exe_path.is_file():
        raise RuntimeError(
            f"MYSTRAN solver executable not found: {solver_exe_path}\n"
            "Download it and place it in solver/ -- see README.md."
        )

    dat_path = _stage_dat_file(bdf_path)
    f06_path = dat_path.with_suffix(".F06")
    op2_path = dat_path.with_suffix(".OP2")

    # Remove stale outputs so a failed run can never be mistaken for a
    # leftover successful one from a previous invocation.
    for stale in (f06_path, op2_path):
        stale.unlink(missing_ok=True)

    try:
        proc = subprocess.run(
            [str(solver_exe_path), str(dat_path)],
            cwd=dat_path.parent,
            # MYSTRAN blocks waiting on stdin for a filename instead of
            # failing cleanly if its input file can't be found -- never let
            # an automated run wait on that.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"MYSTRAN did not finish within {timeout}s (input={dat_path})"
        ) from exc

    stdout = proc.stdout or ""
    errors: list[str] = []

    if not f06_path.is_file():
        errors.append(f"MYSTRAN did not produce an F06 file at {f06_path}")
    else:
        errors.extend(_scan_f06_for_errors(f06_path))

    if not errors and STDOUT_FATAL_HINT.search(stdout):
        errors.append(
            "MYSTRAN stdout reported fatal message(s) "
            f"but none were matched in {f06_path.name} -- inspect it by hand."
        )

    if not errors and proc.returncode != 0:
        errors.append(f"MYSTRAN exited with nonzero return code {proc.returncode}")

    if not errors and not op2_path.is_file():
        errors.append(
            f"No fatal errors detected in F06, but MYSTRAN did not produce "
            f"an OP2 results file at {op2_path}"
        )

    return SolverResult(
        success=not errors,
        dat_path=dat_path,
        f06_path=f06_path,
        op2_path=op2_path,
        returncode=proc.returncode,
        errors=errors,
        stdout=stdout,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the MYSTRAN solver on a Nastran BDF deck and verify success "
            "by parsing the .F06 output for fatal errors (MYSTRAN's exit "
            "code alone cannot be trusted)."
        )
    )
    parser.add_argument(
        "bdf_path",
        nargs="?",
        default=str(DEFAULT_BDF_PATH),
        help=f"Path to the BDF file to solve (default: {DEFAULT_BDF_PATH})",
    )
    parser.add_argument(
        "--solver",
        dest="solver_exe_path",
        default=str(DEFAULT_SOLVER_PATH),
        help=f"Path to the MYSTRAN solver executable (default: {DEFAULT_SOLVER_PATH})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help="Seconds to wait for MYSTRAN before giving up (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    try:
        result = run_solver(args.bdf_path, args.solver_exe_path, timeout=args.timeout)
    except RuntimeError as exc:
        print(f"COULD NOT RUN SOLVER: {exc}")
        return 1

    print(f"Input (staged): {result.dat_path}")
    print(f"F06: {result.f06_path}")
    print(f"OP2: {result.op2_path}")
    print(f"MYSTRAN return code: {result.returncode}")
    print()
    if result.success:
        print("SOLVE SUCCEEDED -- no fatal errors detected in F06.")
        return 0

    print("SOLVE FAILED:")
    for err in result.errors:
        print(f"  - {err}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
