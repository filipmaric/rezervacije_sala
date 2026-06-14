#!/usr/bin/env python3

"""Test the teacher RADIUS backend using values from rezervacije.env."""

import argparse
import getpass
import os
import sys

from pyrad.client import Client
from pyrad.dictionary import Dictionary
import pyrad.packet


def load_env_file(path):
    """Load simple KEY=value pairs from a shell-style env file."""
    values = {}
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            values[key] = value
    return values


def env_value(name, env_file_values, fallback=None):
    """Read a value from the env file first, then from the process env."""
    value = env_file_values.get(name)
    if value:
        return value
    value = os.getenv(name)
    if value:
        return value
    return fallback


def main():
    parser = argparse.ArgumentParser(
        description="Send a teacher RADIUS login request using rezervacije.env."
    )
    parser.add_argument(
        "--env-file",
        default="/var/www/rezervacije/rezervacije.env",
        help="Path to the rezervacije.env file",
    )
    parser.add_argument("--username", required=True, help="Teacher username")
    parser.add_argument(
        "--password",
        help="Teacher password. If omitted, the script will prompt for it.",
    )
    args = parser.parse_args()

    env_file_values = load_env_file(args.env_file)

    backend = env_value("TEACHER_AUTH_BACKEND", env_file_values, "mock").lower()
    server = env_value("TEACHER_RADIUS_SERVER", env_file_values)
    secret = env_value("TEACHER_RADIUS_SECRET", env_file_values)
    dictionary_path = env_value("TEACHER_RADIUS_DICTIONARY", env_file_values)

    missing = [
        name
        for name, value in (
            ("TEACHER_RADIUS_SERVER", server),
            ("TEACHER_RADIUS_SECRET", secret),
            ("TEACHER_RADIUS_DICTIONARY", dictionary_path),
        )
        if not value
    ]
    if missing:
        print(f"Missing required values in env file: {', '.join(missing)}", file=sys.stderr)
        return 2

    if backend != "radius":
        print(
            f"TEACHER_AUTH_BACKEND is set to {backend!r}, not 'radius'.",
            file=sys.stderr,
        )
        return 2

    password = args.password if args.password is not None else getpass.getpass("Teacher password: ")

    client = Client(
        server=server,
        secret=secret.encode("utf-8"),
        dict=Dictionary(dictionary_path),
    )
    request = client.CreateAuthPacket(
        code=pyrad.packet.AccessRequest,
        User_Name=args.username,
    )
    request["User-Password"] = request.PwCrypt(password)

    try:
        reply = client.SendPacket(request)
    except Exception as exc:
        print(f"RADIUS request failed: {exc}", file=sys.stderr)
        return 1

    if reply.code == pyrad.packet.AccessAccept:
        print("Access-Accept")
        return 0

    if reply.code == pyrad.packet.AccessReject:
        print("Access-Reject")
        return 1

    print(f"Unexpected RADIUS reply: {reply.code}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
