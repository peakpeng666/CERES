#!/usr/bin/env python3
"""FUTURE controller-only HTTPS fetch, not currently authorized or executed.

Explicit output argument creates a fresh directory containing exactly the three
public pinned inputs. It cannot install a package, execute downloaded content,
invoke Docker, inherit proxy credentials, or copy host certificates. The future
controller must source-anchor this helper and lock before executing either.
"""
import argparse
import hashlib
import json
from pathlib import Path
import ssl
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Snapshot redirect refused; requires separate review")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    lock = json.loads((root / "ca-inputs.lock.json").read_text())
    if set(lock) != {"schema", "purpose", "snapshot", "signer", "package", "inputs"} or lock["schema"] != 1:
        raise RuntimeError("Invalid reviewed seed lock")
    expected_names = {"jammy-security.InRelease", "jammy-security.main.amd64.Packages.xz", "ca-certificates.deb"}
    if len(lock["inputs"]) != 3 or {r["name"] for r in lock["inputs"]} != expected_names:
        raise RuntimeError("Invalid seed input set")
    # No SSL_CERT_FILE, CURL_CA_BUNDLE or custom context is passed into the image.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    out = args.output.absolute()
    if out.exists() or out.is_symlink() or out.parent.resolve() != out.parent:
        raise RuntimeError("Fresh, nonsymlink output under an existing real parent required")
    out.mkdir(mode=0o755)
    for row in lock["inputs"]:
        if set(row) != {"name", "url", "size", "sha256"}:
            raise RuntimeError("Invalid input record")
        if not row["url"].startswith("https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/"):
            raise RuntimeError("Unexpected snapshot origin")
        if not isinstance(row["size"], int) or not 0 < row["size"] <= 4 * 1024 * 1024:
            raise RuntimeError("Invalid seed size")
        with opener.open(row["url"], timeout=60) as response:
            if response.status != 200 or response.geturl() != row["url"]:
                raise RuntimeError("Unexpected snapshot response")
            data = response.read(row["size"] + 1)
        if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise RuntimeError("Public seed input hash or size mismatch")
        (out / row["name"]).write_bytes(data)
    print("Public CA seed materialized; native image signature verification and install remain pending.")


if __name__ == "__main__":
    main()
