"""Command line: wormscore <command> <project folder> ..."""
import argparse
import json
import sys
from pathlib import Path

from . import __version__


def main(argv=None):
    p = argparse.ArgumentParser(prog="wormscore", description="Blind scoring and measurement of neurons in confocal stacks.")
    p.add_argument("--version", action="version", version=f"wormscore {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a project folder with a config.json to edit")
    s.add_argument("project")
    s.add_argument("--name", default="My project")
    s = sub.add_parser("demo", help="create a demo project with simulated worms")
    s.add_argument("project")
    s.add_argument("--n", type=int, default=10, help="simulated worms per genotype")
    s.add_argument("--real", action="store_true",
                   help="download 4 real worm heads (about 150 MB, CC BY 4.0) instead of simulating")
    s = sub.add_parser("copy", help="copy a data folder and verify every file by SHA-256")
    s.add_argument("source")
    s.add_argument("destination")
    s = sub.add_parser("inventory", help="list every file and image in the project's sources")
    s.add_argument("project")
    s = sub.add_parser("prepare", help="build the blind image set, key and automatic measurements")
    s.add_argument("project")
    s.add_argument("--reassign-codes", action="store_true", help="new codes (only allowed before any scoring)")
    s.add_argument("--add-new-images", dest="add", action="store_true",
                   help="code only images not yet in the key; existing codes are kept")
    s = sub.add_parser("app", help="open the scoring app")
    s.add_argument("project")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s = sub.add_parser("unblind", help="join scores, measurements and the key (after scoring)")
    s.add_argument("project")
    s.add_argument("--i-have-finished-scoring", dest="finished", action="store_true")
    s.add_argument("--anyway", action="store_true", help="unblind below the planned minimum number of scores")
    s = sub.add_parser("update", help="check GitHub for a newer version; --install or --rollback the downloaded app")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="download, check and install it when this command ends")
    g.add_argument("--rollback", action="store_true", help="go back to the previous version kept by the last update")
    s.add_argument("--anyway", action="store_true", help="install even though a project is between build and unblind")

    a = p.parse_args(argv)
    try:
        if a.cmd == "init":
            from .config import template
            proj = Path(a.project)
            proj.mkdir(parents=True, exist_ok=True)
            cfg = proj / "config.json"
            if cfg.exists():
                sys.exit(f"{cfg} already exists; not overwriting.")
            cfg.write_text(json.dumps(template(a.name), indent=2), encoding="utf-8")
            print(f"Created {cfg}. Edit 'sources' to point at your images, then run 'wormscore inventory {proj}'.")
        elif a.cmd == "demo":
            if a.real:
                from .realdemo import make_real_demo
                make_real_demo(a.project)
            else:
                from .demo import make_demo
                make_demo(a.project, n_per_group=a.n)
            print(f"Next: wormscore inventory {a.project} && wormscore prepare {a.project} && wormscore app {a.project}")
        elif a.cmd == "copy":
            from .copydata import copy_verified
            copy_verified(a.source, a.destination)
        elif a.cmd == "inventory":
            from .pipeline import inventory
            inventory(a.project)
        elif a.cmd == "prepare":
            from .pipeline import prepare
            prepare(a.project, reassign=a.reassign_codes, add=a.add)
        elif a.cmd == "app":
            from .server import serve
            serve(a.project, port=a.port, open_browser=not a.no_browser)
        elif a.cmd == "unblind":
            from .unblind import unblind
            unblind(a.project, finished=a.finished, anyway=a.anyway)
        elif a.cmd == "update":
            update_command(a)
    except FileNotFoundError as exc:
        sys.exit(str(exc))


def update_command(a):
    from . import update as U
    from .home import load_settings, project_status
    try:
        if a.rollback:
            back = U.rollback(relaunch=False)
            print(f"wormscore {back} is put back when this command ends.")
            return
        info = U.check(timeout=15)
        if not U.is_newer(info["version"]):
            print(f"wormscore {__version__} is the latest version.")
            return
        print(f"wormscore {info['version']} is available (you have {__version__}).")
        kind = U.install_kind()
        if kind == "pip":
            print(f"This copy was installed with Python. Update it with:  {U.PIP_COMMAND}")
            return
        if not a.install:
            print("Install it with:  wormscore update --install")
            return
        projects = U.unfinished_projects([project_status(p) for p in load_settings()["recent"]])
        if projects and not a.anyway:
            sys.exit(U.warning_text(projects, info["version"]) + " Add --anyway to update now.")
        U.install(info, say=print, relaunch=False)
        print("The new version replaces this copy when this command ends.")
    except U.UpdateError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
