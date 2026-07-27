"""
Push a freshly-renewed Schwab token pair to GitHub Actions secrets.

Run this right after the manual browser login step (refresh_token.py) on
Windows. It reads the resulting token file and updates SCHWAB_REFRESH_TOKEN
and SCHWAB_ACCESS_TOKEN in GitHub Actions secrets, so you don't have to
copy-paste them by hand.

Requires:
    pip install requests pynacl
    GITHUB_TOKEN env var — a GitHub personal access token (fine-grained,
    scoped to this repo, with "Secrets" repository permission set to
    Read and write).

Usage:
    python push_schwab_secrets.py
        Finds the token file automatically (current folder, this script's
        folder, your home folder, or ~/scanner).

    python push_schwab_secrets.py --token-path C:\\Users\\John\\schwab_token.json
        Point at the file explicitly.

    python push_schwab_secrets.py --access-token <AT> --refresh-token <RT>
        Paste the values straight from refresh_token.py's output — no file
        needed.

    Add --repos eastbiz/reports to target a single repo.
"""
import argparse
import base64
import glob
import json
import os
import sys

import requests
from nacl import encoding, public


def find_token_file(explicit_path: str | None) -> str:
    """Locate the token file. refresh_token.py writes it relative to whatever
    directory it was run from, which is not always the scanner folder."""
    if explicit_path:
        if not os.path.exists(explicit_path):
            sys.exit(f"❌ No such file: {explicit_path}")
        return explicit_path

    home = os.path.expanduser("~")
    candidates = [
        "schwab_token.json",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "schwab_token.json"),
        os.path.join(home, "schwab_token.json"),
        os.path.join(home, "scanner", "schwab_token.json"),
    ]
    # Also accept any *token*.json sitting in the scanner folder or home dir.
    for d in (os.getcwd(), os.path.join(home, "scanner"), home):
        candidates.extend(sorted(glob.glob(os.path.join(d, "*token*.json"))))

    seen = set()
    for path in candidates:
        real = os.path.abspath(path)
        if real in seen or not os.path.exists(real):
            continue
        seen.add(real)
        try:
            with open(real) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        token = data.get("token", data)
        if token.get("access_token") and token.get("refresh_token"):
            print(f"   Using token file: {real}")
            return real

    sys.exit(
        "❌ Couldn't find a Schwab token file containing access_token + refresh_token.\n"
        "   Looked in: current folder, this script's folder, your home folder,\n"
        "   and the scanner folder.\n"
        "   Fix: pass the path explicitly, e.g.\n"
        "     python push_schwab_secrets.py --token-path C:\\Users\\John\\schwab_token.json\n"
        "   Or paste the values directly:\n"
        "     python push_schwab_secrets.py --access-token <AT> --refresh-token <RT>"
    )


def load_tokens(token_path: str) -> tuple[str, str]:
    with open(token_path) as f:
        data = json.load(f)
    # schwab-py wraps the token under "token"; fall back to a flat shape.
    token = data.get("token", data)
    access_token = token.get("access_token", "")
    refresh_token = token.get("refresh_token", "")
    if not access_token or not refresh_token:
        sys.exit(f"❌ Couldn't find access_token/refresh_token in {token_path}")
    return access_token, refresh_token


def encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    key = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(key)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def push_secret(session: requests.Session, repo: str, key_id: str,
                 public_key: str, name: str, value: str) -> None:
    encrypted_value = encrypt_secret(public_key, value)
    r = session.put(
        f"https://api.github.com/repos/{repo}/actions/secrets/{name}",
        json={"encrypted_value": encrypted_value, "key_id": key_id},
        timeout=10,
    )
    if r.status_code in (201, 204):
        print(f"   ✅ {name} updated")
    else:
        sys.exit(f"❌ Failed to update {name}: {r.status_code} {r.text}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-path", default=os.environ.get("SCHWAB_TOKEN_PATH"))
    ap.add_argument("--access-token", help="Paste the ACCESS value directly instead of reading a file")
    ap.add_argument("--refresh-token", help="Paste the REFRESH value directly instead of reading a file")
    ap.add_argument("--repos", default="eastbiz/whale-intelligence,eastbiz/reports")
    args = ap.parse_args()
    repos = [r.strip() for r in args.repos.split(",") if r.strip()]

    gh_token = os.environ.get("GITHUB_TOKEN")
    if not gh_token:
        sys.exit(
            "❌ GITHUB_TOKEN env var not set.\n"
            "   Create a fine-grained PAT scoped to these repos with the "
            "\"Secrets\" repository permission set to Read and write, "
            "then set it as a permanent GITHUB_TOKEN environment variable."
        )

    if args.access_token and args.refresh_token:
        access_token, refresh_token = args.access_token, args.refresh_token
    elif args.access_token or args.refresh_token:
        sys.exit("❌ Pass BOTH --access-token and --refresh-token, or neither.")
    else:
        access_token, refresh_token = load_tokens(find_token_file(args.token_path))

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })

    for repo in repos:
        r = session.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", timeout=10)
        if r.status_code != 200:
            sys.exit(f"❌ Couldn't fetch public key for {repo}: {r.status_code} {r.text}")
        key_data = r.json()

        print(f"   Pushing tokens to {repo} secrets...")
        push_secret(session, repo, key_data["key_id"], key_data["key"], "SCHWAB_ACCESS_TOKEN", access_token)
        push_secret(session, repo, key_data["key_id"], key_data["key"], "SCHWAB_REFRESH_TOKEN", refresh_token)
    print("✅ Done — GitHub Secrets updated on all repos.")


if __name__ == "__main__":
    main()
