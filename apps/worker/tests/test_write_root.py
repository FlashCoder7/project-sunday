"""file_write must work when the allowed root is a symlink.

On the Mac the repo moved to ~/Developer/project-sunday and ~/Projects/PersonalAI
is now a symlink to it. `config` used the default path unresolved while
`file_ops` resolves the file it is about to write, so the comparison in
`relative_to` was between a resolved path and a symlink path: no write under the
root matched, and every `file_write` failed with "path outside allowed root".

The test runs the real modules in a subprocess so the root is read exactly as
the worker reads it — at import time, from the environment and `$HOME`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if sys.version_info < (3, 10):
    # The worker's own syntax needs 3.10+ (the Mac's /usr/bin/python3 is 3.9).
    print(f"SKIP: needs Python 3.10+ for the worker modules (this is {sys.version.split()[0]}).")
    sys.exit(0)

WORKER = str(Path(__file__).resolve().parent.parent)
failures = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ok  {label}")
    else:
        failures.append(f"{label} {detail}")
        print(f"  ✗   {label} {detail}")


CHILD = r"""
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
import config
from executors.file_ops import file_write

inside, outside = sys.argv[2], sys.argv[3]

async def main():
    wrote = await file_write(inside, "hello")
    refused = await file_write(outside, "hello")
    print(json.dumps({
        "root": str(config.ALLOWED_WRITE_ROOT),
        "inside": wrote,
        "outside": refused,
    }))

asyncio.run(main())
"""


def run_child(env_extra: dict, inside: Path, outside: Path) -> dict:
    env = dict(os.environ)
    env.pop("ALLOWED_WRITE_ROOT", None)
    env.update({
        "SUPABASE_URL": "http://localhost",
        "SUPABASE_SERVICE_ROLE_KEY": "test",
        "GEMINI_API_KEY": "test",
    })
    env.update(env_extra)
    out = subprocess.run(
        [sys.executable, "-c", CHILD, WORKER, str(inside), str(outside)],
        capture_output=True, text=True, env=env,
    )
    if out.returncode != 0:
        return {"error": out.stderr[-500:]}
    return json.loads(out.stdout.strip().splitlines()[-1])


def main():
    tmp = Path(tempfile.mkdtemp(prefix="sunday-write-root-"))
    try:
        # The Mac layout: $HOME/Projects/PersonalAI is a symlink to the real repo.
        home = tmp / "home"
        real_repo = tmp / "Developer" / "project-sunday"
        (home / "Projects").mkdir(parents=True)
        real_repo.mkdir(parents=True)
        (home / "Projects" / "PersonalAI").symlink_to(real_repo)

        print("── the default root, through the symlink ──────────────")
        result = run_child(
            {"HOME": str(home)},
            inside=home / "Projects" / "PersonalAI" / "notes" / "today.md",
            outside=tmp / "elsewhere" / "escape.md",
        )
        if "error" in result:
            print("  ✗   the child could not load the worker:", result["error"])
            sys.exit(1)
        check("the child ran", True)

        root = Path(result.get("root", ""))
        check("config resolved the symlinked default to its target",
              root == real_repo.resolve(), f"root={root} real={real_repo.resolve()}")
        check("a write under the symlinked root is allowed",
              result.get("inside", "").startswith("Wrote "), result.get("inside"))
        landed = real_repo / "notes" / "today.md"
        check("...and the file landed on the real target",
              landed.is_file() and landed.read_text() == "hello",
              "file missing" if not landed.is_file() else "wrong content")
        check("a write outside the root is refused",
              result.get("outside") == "Error: path outside allowed root",
              result.get("outside"))
        check("...and nothing was created there", not (tmp / "elsewhere").exists())

        print("\n── the container's absolute root is unchanged ─────────")
        # /data on the Linux host is a plain absolute path, not a symlink: it must
        # still be accepted as given, and still bound the writes.
        data = tmp / "data-root"
        data.mkdir()
        result = run_child(
            {"HOME": str(home), "ALLOWED_WRITE_ROOT": str(data)},
            inside=data / "logs" / "worker.log",
            outside=tmp / "outside-data" / "nope.txt",
        )
        check("the configured root is used as given",
              Path(result.get("root", "")) == data.resolve(), result.get("root"))
        check("a write under it is allowed",
              result.get("inside", "").startswith("Wrote "), result.get("inside"))
        check("a write outside it is refused",
              result.get("outside") == "Error: path outside allowed root",
              result.get("outside"))

        print("\n── an explicitly symlinked env root also matches ──────")
        link = tmp / "link-root"
        link.symlink_to(real_repo)
        result = run_child(
            {"HOME": str(home), "ALLOWED_WRITE_ROOT": str(link)},
            inside=link / "env-written.md",
            outside=tmp / "nope2.md",
        )
        check("the env root is resolved too",
              Path(result.get("root", "")) == real_repo.resolve(), result.get("root"))
        check("a write through it is allowed",
              result.get("inside", "").startswith("Wrote "), result.get("inside"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"✗ {len(failures)} failure(s):\n")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("✓ all write-root tests passed")


main()
