"""Idempotent schema bootstrap for the first pilot release.

This initial schema has no prior production migrations. Future schema changes
must use explicit migrations instead of relying on create_all to alter tables.
"""
from app.db import initialize_database


if __name__ == "__main__":
    initialize_database()
    print("Splash schema initialized (including PostgreSQL pgvector).")
