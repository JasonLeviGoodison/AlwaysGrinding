from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import load_config, parse_process_names, run_setup
from .doctor import render_report
from .logging_utils import configure_logging
from .service import install_service, uninstall_service


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv if argv is not None else _default_argv())
    configure_logging(args.log_level)

    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _default_argv() -> list[str]:
    if len(sys.argv) <= 1:
        return ["run"]
    return sys.argv[1:]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lid-guard",
        description="Keep your laptop awake on lid close while selected coding agents are running.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set the CLI log level.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Start lid-guard in the foreground.")
    run_parser.add_argument(
        "--watch-process",
        action="append",
        dest="watch_processes",
        default=None,
        help="Override watched processes for this run. Repeat the flag for multiple names.",
    )
    run_parser.add_argument(
        "--process-poll-interval",
        type=float,
        default=None,
        help="Override the process poll interval in seconds for this run.",
    )
    run_parser.add_argument(
        "--lid-poll-interval",
        type=float,
        default=None,
        help="Override the lid poll interval in seconds for this run.",
    )
    run_parser.set_defaults(func=_run_command)

    setup_parser = subparsers.add_parser("setup", help="Interactive configuration wizard.")
    setup_parser.set_defaults(func=_setup_command)

    doctor_parser = subparsers.add_parser("doctor", help="Validate runtime dependencies and environment.")
    doctor_parser.set_defaults(func=_doctor_command)

    service_parser = subparsers.add_parser("service", help="Install or remove a background service.")
    service_subparsers = service_parser.add_subparsers(dest="service_command", required=True)

    service_install = service_subparsers.add_parser("install", help="Install the current CLI as a user service.")
    service_install.add_argument(
        "--write-only",
        action="store_true",
        help="Write service files but do not enable or start the service.",
    )
    service_install.set_defaults(func=_service_install_command)

    service_uninstall = service_subparsers.add_parser("uninstall", help="Remove the installed user service.")
    service_uninstall.add_argument(
        "--keep-running",
        action="store_true",
        help="Remove service files but skip the service stop/disable step.",
    )
    service_uninstall.set_defaults(func=_service_uninstall_command)

    return parser


def _run_command(args: argparse.Namespace) -> int:
    config = load_config()

    if args.watch_processes:
        config["watched_processes"] = parse_process_names(",".join(args.watch_processes))
    if args.process_poll_interval is not None:
        config["process_poll_interval_seconds"] = float(args.process_poll_interval)
    if args.lid_poll_interval is not None:
        config["lid_poll_interval_seconds"] = float(args.lid_poll_interval)

    if sys.platform == "linux":
        from .platform_linux import LinuxLidGuard

        LinuxLidGuard(config).run()
        return 0

    if sys.platform == "darwin":
        from .platform_macos import MacOSLidGuard

        MacOSLidGuard(config).run()
        return 0

    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def _setup_command(_args: argparse.Namespace) -> int:
    run_setup()
    return 0


def _doctor_command(_args: argparse.Namespace) -> int:
    exit_code, report = render_report()
    print(report, end="")
    return exit_code


def _service_install_command(args: argparse.Namespace) -> int:
    path = install_service(enable=not args.write_only)
    print(path)
    return 0


def _service_uninstall_command(args: argparse.Namespace) -> int:
    removed = uninstall_service(disable=not args.keep_running)
    for path in removed:
        print(path)
    return 0
