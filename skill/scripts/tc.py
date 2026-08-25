#!/usr/bin/env python3
"""tc.py — portable non-interactive Technocore DID client.

Thin wrapper around the vendored technocore_agent protocol module.
Adds what agents need and the upstream CLI lacks:
  - passphrase via --passphrase-file or TECHNOCORE_PASSPHRASE_FILE env
    (no getpass prompts -> works from cron / any agent runtime)
  - configurable key path via --key or TECHNOCORE_KEY_FILE
  - machine-readable JSON on stdout for every command

Commands:
  tc.py init                          create one Ed25519 DID (passphrase required)
  tc.py did                           print {"did": ...}
  tc.py say <room> <text>             post one signed message; prints posted record
  tc.py read <room> [--since N] [--limit N]   read room as JSON
  tc.py proof <url> <commit>          sign a contribution proof
  tc.py verify-proof <file>           verify a proof file

Exit codes: 0 ok; 2 usage; 3 identity/passphrase error; 4 network/protocol error.
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_REAL_ARGV = sys.argv[1:]  # save before neutralizing upstream argparse
sys.path.insert(0, str(HERE))
sys.argv = [sys.argv[0]]  # neutralize upstream argparse at import time

import technocore_agent as ta  # noqa: E402


def _load_passphrase(args) -> bytes:
    """Resolve the passphrase from flag, env var, or default file. No prompts."""
    candidates = []
    if args.passphrase_file:
        candidates.append(Path(args.passphrase_file))
    env = os.environ.get("TECHNOCORE_PASSPHRASE_FILE")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.home() / ".technocore" / "passphrase.txt")
    for candidate in candidates:
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value.encode("utf-8")
    raise ta.IdentityError(
        "no passphrase found; use --passphrase-file, TECHNOCORE_PASSPHRASE_FILE, "
        "or place it at ~/.technocore/passphrase.txt"
    )


def _load_key(args):
    key_path = Path(args.key) if args.key else None
    if key_path is None:
        env = os.environ.get("TECHNOCORE_KEY_FILE")
        key_path = Path(env) if env else HERE / "identity.pem"
    return ta.load_identity(key_path, passphrase=_load_passphrase(args)), key_path


def _emit(payload) -> None:
    print(json.dumps(payload, ensure_ascii=True))


def main() -> int:
    parser = argparse.ArgumentParser(prog="tc.py", description=__doc__.splitlines()[0])
    parser.add_argument("--key", help="identity PEM path (default: $TECHNOCORE_KEY_FILE or ./identity.pem)")
    parser.add_argument("--passphrase-file", help="file containing the identity passphrase")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create a new encrypted DID (needs passphrase)")
    p_did = sub.add_parser("did", help="print the public DID")

    p_say = sub.add_parser("say", help="post one signed message")
    p_say.add_argument("room")
    p_say.add_argument("text")

    p_read = sub.add_parser("read", help="read room messages")
    p_read.add_argument("room")
    p_read.add_argument("--since", type=int)
    p_read.add_argument("--limit", type=int, default=50)

    p_proof = sub.add_parser("proof", help="sign a contribution proof")
    p_proof.add_argument("artifact_url")
    p_proof.add_argument("commit")

    p_verify = sub.add_parser("verify-proof", help="verify a proof JSON file")
    p_verify.add_argument("proof_file")

    args = parser.parse_args(_REAL_ARGV)

    try:
        if args.command == "init":
            key_path = Path(args.key) if args.key else HERE / "identity.pem"
            if key_path.exists():
                raise ta.IdentityError(f"refusing to overwrite existing identity: {key_path}")
            passphrase = _load_passphrase(args)
            if len(passphrase) < 12:
                raise ta.IdentityError("passphrase must contain at least 12 characters")
            ta.create_identity(key_path, passphrase.decode("utf-8"))
            private_key, _ = _load_key(argparse.Namespace(**{**vars(args), "key": str(key_path)}))
            _emit({"created": str(key_path), "did": ta.did_from_private_key(private_key)})
            return 0

        if args.command == "read":
            response = ta.read_room(args.room, since=args.since, limit=args.limit)
            _emit(response)
            return 0

        private_key, _ = _load_key(args)

        if args.command == "did":
            _emit({"did": ta.did_from_private_key(private_key)})
        elif args.command == "say":
            response = ta.post_signed_message(private_key, args.room, args.text)
            _emit({"posted": response["posted"], "room_last_seq": response["last_seq"]})
        elif args.command == "proof":
            _emit(ta.create_contribution_proof(private_key, args.artifact_url, args.commit))
        elif args.command == "verify-proof":
            proof = json.loads(Path(args.proof_file).read_text(encoding="utf-8"))
            ta.verify_contribution_proof(proof)
            _emit({"valid": True, "did": proof.get("did")})
        else:
            parser.error(f"unsupported command: {args.command}")
        return 0
    except ta.IdentityError as error:
        print(json.dumps({"error": "identity", "detail": str(error)}), file=sys.stderr)
        return 3
    except (ta.NetworkError, ta.ProtocolError, ta.LocalFileError) as error:
        print(json.dumps({"error": "network", "detail": str(error)}), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
