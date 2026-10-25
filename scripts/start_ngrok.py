"""Start the local webhook tunnel with an explicitly configured stable domain."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate and show URLs without starting a tunnel')
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    domain = os.getenv('NGROK_DOMAIN', '').strip()
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.ngrok-free\.(?:app|dev)', domain):
        parser.error('Set NGROK_DOMAIN in .env to your assigned ngrok development hostname, without https:// or a path')
    url = 'https://' + domain
    print('Fixed tunnel: ' + url, flush=True)
    print('Lemon Squeezy webhook: ' + url + '/billing/webhooks/lemonsqueezy', flush=True)
    print('Browser app: http://localhost:5000 (keep BASE_URL unchanged)', flush=True)
    if args.check:
        return 0
    executable = shutil.which('ngrok')
    if not executable:
        parser.error('ngrok CLI was not found. Install it and configure its authtoken first')
    # Explicit URL prevents falling back to a different hostname. Keep the token
    # in ngrok CLI configuration; never include it in command arguments or logs.
    try:
        return subprocess.call([executable, 'http', '5000', '--url=' + url])
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
