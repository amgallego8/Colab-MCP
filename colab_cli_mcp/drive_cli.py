"""Terminal commands for Drive sign-in: `colab-cli-mcp-drive login [--own|--shared] | logout | status`.

Run by the HUMAN in a terminal. It is deliberately not an MCP tool: the model must never see
the consent link, the authorization code or the token.
"""

import json
import sys

from . import drive
from .core import ToolError

USAGE = """colab-cli-mcp-drive login [--own|--shared] | logout | status

Before the first login you need your OWN OAuth client (see README, "Google Drive setup"):
save the downloaded Desktop-app client JSON as
  {client}
"""


def _need_google():
    try:
        import google_auth_oauthlib.flow  # noqa: F401
        import requests  # noqa: F401
    except ImportError:
        sys.exit("Missing packages. Install with:  pip install 'colab-cli-mcp[drive]'   (or: uv sync --extra drive)")


EXPLAIN = """Which Google OAuth client do you want to sign in with?

  own    - the one YOU created in your own Google Cloud project ({own}).
           You own the project and answer to Google for it. Recommended.
  shared - the one shipped with this package by its maintainer.
           Nothing to set up, but the maintainer's project is the app Google shows you.
           Sign-in, scope (drive.file only), token and guardrails are the same either way.

Using this tool means accepting TERMS.md (https://github.com/amgallego8/Colab-MCP/blob/main/TERMS.md)
and Google's own terms.
"""


def choose_client(flags: list[str]):
    """Never use the shared client without an explicit choice (flag or typed answer)."""
    found = drive.available_clients()
    want = "own" if "--own" in flags else "shared" if "--shared" in flags else None
    if "--own" in flags and "--shared" in flags:
        print("Choose one of --own or --shared.")
        return None
    if want:
        if want not in found:
            print(f"The {want} client is not available." + (f" Expected {drive.client_path()}" if want == "own" else " This package does not ship one."))
            return None
        return found[want]
    if not found:
        print("No OAuth client found.\n" + USAGE.format(client=drive.client_path()))
        return None
    if not sys.stdin.isatty():
        print("Not an interactive terminal: pass --own or --shared. Available: " + ", ".join(found))
        return None
    print(EXPLAIN.format(own=drive.client_path()))
    if list(found) == ["own"]:
        print("Using your own client.")
        return found["own"]
    options = " / ".join(found)
    default = "own" if "own" in found else None
    while True:
        ans = input(f"Choose [{options}]{' (Enter = own)' if default else ''}: ").strip().lower() or (default or "")
        if ans in found:
            return found[ans]
        if ans in {"q", "quit", "exit"}:
            return None


def login() -> int:
    _need_google()
    from google_auth_oauthlib.flow import InstalledAppFlow

    client = choose_client(sys.argv[2:])
    if client is None:
        return 1
    try:
        data = json.loads(client.read_text(encoding="utf-8"))
    except ValueError:
        print("The client file is not valid JSON.")
        return 1
    if "installed" not in data:
        print("The client must be of type 'Desktop app' (the JSON should start with {\"installed\": ...}).")
        return 1
    print(f"Requesting ONLY this permission: {drive.SCOPE}\n(access to files created by this tool; nothing else in your Drive)\n")
    flow = InstalledAppFlow.from_client_secrets_file(str(client), scopes=[drive.SCOPE])
    creds = flow.run_local_server(host="localhost", port=0, prompt="consent", access_type="offline")
    if set(creds.scopes or []) != {drive.SCOPE} or not creds.refresh_token:
        print("Google did not grant exactly the drive.file scope (or returned no refresh token). Nothing was saved.")
        return 1
    drive.save_token(creds.to_json())
    drive.reset()
    drive.audit("login", ok=True)
    print(f"Signed in. Token saved at {drive.token_path()} (readable only by you).")
    print("Never share that file. To remove access: colab-cli-mcp-drive logout")
    return 0


def logout() -> int:
    tp = drive.token_path()
    if not tp.exists():
        print("No token stored.")
        return 0
    try:
        token = json.loads(tp.read_text(encoding="utf-8")).get("refresh_token")
    except ValueError:
        token = None
    revoked = False
    if token:
        _need_google()
        import requests

        try:
            revoked = requests.post("https://oauth2.googleapis.com/revoke", data={"token": token}, timeout=15).status_code == 200
        except requests.RequestException:
            revoked = False
    tp.unlink()
    drive.reset()
    drive.audit("logout", ok=True, revoked=revoked)
    print("Token deleted locally." + (" Access revoked at Google." if revoked else " Could not confirm revocation at Google: remove the app at https://myaccount.google.com/permissions"))
    return 0


def status() -> int:
    tp = drive.token_path()
    found = drive.available_clients()
    print("clients     : " + (", ".join(f"{k} ({v})" for k, v in found.items()) if found else "NONE (see README, Google Drive setup)"))
    print(f"token file  : {tp} ({'found' if tp.exists() else 'not signed in'})")
    if tp.exists():
        try:
            drive.check_private(tp)
            print("permissions : ok (owner only)")
        except ToolError as exc:
            print(f"permissions : PROBLEM - {exc}")
        try:
            scopes = json.loads(tp.read_text(encoding="utf-8")).get("scopes")
            print(f"scopes      : {scopes}")
        except ValueError:
            print("scopes      : token file unreadable")
    print(f"audit log   : {drive.audit_path()}")
    print(f"tools on    : {'yes' if drive.enabled() else 'no (set COLAB_MCP_DRIVE=1 in the MCP config)'}")
    return 0


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"login": login, "logout": logout, "status": status}.get(cmd)
    if not fn:
        print(USAGE.format(client=drive.client_path()))
        sys.exit(2)
    sys.exit(fn())


if __name__ == "__main__":
    main()
