"""Prints the hash for OWNER_PASSWORD_HASH in .env: `uv run python -m app.hashpw`.
Only the hash goes to stdout; the prompts go to the terminal."""

import getpass
import sys

from werkzeug.security import generate_password_hash


def main():
    password = getpass.getpass("Password: ")
    if not password:
        sys.exit("Empty password, nothing printed.")
    if getpass.getpass("Again: ") != password:
        sys.exit("The passwords do not match, nothing printed.")
    print(generate_password_hash(password))


if __name__ == "__main__":
    main()
