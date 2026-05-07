"""Print the dev bearer token for a tenant.

Usage:
    python -m scripts.token tenant-a
    # → bearer token (the HMAC stub)
"""

import sys

from app.tenancy.middleware import expected_token

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m scripts.token <tenant_id>", file=sys.stderr)
        sys.exit(2)
    print(expected_token(sys.argv[1]))
