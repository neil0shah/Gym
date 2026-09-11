"""Create an account on someone's behalf, without them visiting /signup.

Anyone with the deployment's URL can already create their own account at
/signup — this script exists for creating one yourself instead (e.g. you
want to hand someone a working login rather than ask them to sign up).

Run from the project root, with the app's dependencies already installed
(the same environment you run the app itself in):

    python3 -m app.create_user friend@example.com

Prompts for a password (not shown on screen as you type), creates the
account, and seeds it with the same default exercise list a fresh
deployment starts with. The new account's data — exercises, groups,
workout history — is completely separate from every other account's;
nothing is shared.
"""
import getpass
import sys

import bcrypt

from app.database import Base, SessionLocal, engine, run_migrations
from app import models, seed_data


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 -m app.create_user <email>")
        sys.exit(1)
    email = sys.argv[1].strip().lower()

    Base.metadata.create_all(bind=engine)
    run_migrations()

    db = SessionLocal()
    try:
        existing = db.query(models.User).filter(models.User.email == email).first()
        if existing is not None:
            print(f"A user with email {email!r} already exists.")
            sys.exit(1)

        password = getpass.getpass(f"Choose a password for {email}: ")
        confirm = getpass.getpass("Confirm password: ")
        if not password:
            print("Password can't be empty.")
            sys.exit(1)
        if password != confirm:
            print("Passwords didn't match.")
            sys.exit(1)

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode()
        user = models.User(email=email, password_hash=password_hash)
        db.add(user)
        db.commit()
        db.refresh(user)
        seed_data.seed_if_empty(db, user_id=user.id)
        print(f"Created account for {email} with the default exercise list. They can log in now.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
