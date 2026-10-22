"""Whether this Python's TLS refuses certificates it must not trust.

Run inside the image (`docker run --rm --entrypoint python3 <image>
tools/tls_trust_check.py`, from the build tree, or piped to `python3 -`).
Each site below serves a certificate from a chain that has been distrusted;
connecting to one means the trust store still accepts it. Exit 1 if any
connects, 2 if a site could not be reached at all (no answer either way).

- dv-g3b-emudhra.we-have-a-problem.com: an intermediate of eMudhra's whose
  private key was guessable (October 2026, CVE-2026-111943; Debian
  DSA-6572-1 removed emSign ECC Root CA - G3 from the trust store).
"""
import socket
import ssl
import sys

SITES = ["dv-g3b-emudhra.we-have-a-problem.com"]


def main() -> int:
    context = ssl.create_default_context()
    trusted, unreachable = [], []
    for host in SITES:
        try:
            with socket.create_connection((host, 443), timeout=15) as raw, \
                 context.wrap_socket(raw, server_hostname=host):
                trusted.append(host)
        except ssl.SSLCertVerificationError as exc:
            print(f"refused {host}: {exc.verify_message}")
        except OSError as exc:
            unreachable.append(host)
            print(f"no answer from {host}: {exc}")
    for host in trusted:
        print(f"TRUSTED {host}: this image accepts a distrusted certificate")
    print(ssl.OPENSSL_VERSION)
    return 1 if trusted else 2 if unreachable else 0


if __name__ == "__main__":
    sys.exit(main())
