"""Compare the live Streamlit app's deploy target with GitHub.

The public site redirects browsers through a login shell, so the HTML is not
the app. Streamlit still exposes the deploy coordinates without a login:

  GET https://<app>/api/v2/app/disambiguate

That payload names the GitHub repo, branch, and entry file. This script
reads it and prints the tip commit of that branch.
"""
from __future__ import annotations

import json
import sys
import urllib.request

LIVE = "https://stock-analyzergit-ijue4vuwb7kuvizn62fema.streamlit.app"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "stock-analyzer-version-check"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> int:
    deploy = _get_json(f"{LIVE}/api/v2/app/disambiguate")
    status = _get_json(f"{LIVE}/api/v2/app/status")
    owner = deploy["owner"]
    repo = deploy["repository"]
    branch = deploy["branch"]
    module = deploy["mainModule"]
    tip = _get_json(f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}")
    sha = tip["sha"]
    when = tip["commit"]["committer"]["date"]
    subject = tip["commit"]["message"].splitlines()[0]

    print(f"live_host: {deploy.get('host')}")
    print(f"deploy_target: {owner}/{repo}@{branch} ({module})")
    print(f"github_tip: {sha}")
    print(f"github_tip_date: {when}")
    print(f"github_tip_subject: {subject}")
    print(f"streamlit_version: {status.get('streamlitVersion')}")
    print(f"viewer_auth_enabled: {status.get('viewerAuthEnabled')}")
    print(f"app_status_code: {status.get('status')}")
    print(
        "note: this API does not return the container's commit SHA. "
        "It returns the branch Streamlit is configured to deploy. "
        "Community Cloud rebuilds that branch on push."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
