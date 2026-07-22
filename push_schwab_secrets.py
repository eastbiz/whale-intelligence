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

By default it pushes to BOTH repos that need the Schwab token:
    - eastbiz/whale-intelligence  (the public scanner)
    - eastbiz/reports             (the private performance-review system)
so a single weekly run keeps them in sync. Your PAT must have "Secrets:
Read and write" on BOTH repos (fine-grained PATs can list multiple repos).

Usage:
    python push_schwab_secrets.py [--token-path schwab_token.json]
                                   [--repo owner/name]   # repeatable; overrides
                                                          # the default pair
"""
import argparse
import base64
import json
import os
import sys

import requests
from nacl import encoding, public


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
    ap.add_argument("--token-path", default=os.environ.get("SCHWAB_TOKEN_PATH", "schwab_token.json"))
    ap.add_argument("--repo", action="append", dest="repos",
                    help="Repo to update (owner/name). Repeatable. "
                         "Omit to update both whale-intelligence and reports.")
    args = ap.parse_args()
    repos = args.repos or ["eastbiz/whale-intelligence", "eastbiz/reports"]

    gh_token = os.environ.get("GITHUB_TOKEN")
    if not gh_token:
        sys.exit(
            "❌ GITHUB_TOKEN env var not set.\n"
            "   Create a fine-grained PAT scoped to this repo with the "
            "\"Secrets\" repository permission set to Read and write, "
            "then set it as a permanent GITHUB_TOKEN environment variable."
        )

    access_token, refresh_token = load_tokens(args.token_path)

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
    print(f"✅ Done — updated {len(repos)} repo(s): {', '.join(repos)}")


if __name__ == "__main__":
    main()
