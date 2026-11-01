import os

import psycopg
from psycopg import errors

# For local development only. Real projects keep credentials out of code (env vars, .env file).
DSN = os.environ.get(
    "MEETSYNC_DSN",
    "postgresql://meetsync:devpassword@localhost:5432/meetsync",
)


def main() -> None:
    # `with connect(...)` is one TRANSACTION: it commits when the block ends normally
    # and rolls back if an exception escapes.
    with psycopg.connect(DSN) as conn:
        # 1. Parameterized query: values go in the tuple, NEVER into the string via f-strings
        #    (that is how SQL injection happens). %s is a placeholder for every type.
        #    ON CONFLICT = "upsert": insert, or update the username if this Telegram user exists.
        user_id = conn.execute(
            """
            INSERT INTO users (telegram_user_id, telegram_username)
            VALUES (%s, %s)
            ON CONFLICT (telegram_user_id)
            DO UPDATE SET telegram_username = EXCLUDED.telegram_username
            RETURNING user_id
            """,
            (111222333, "test_user"),
        ).fetchone()[0]

        group_id = conn.execute(
            """
            INSERT INTO groups (telegram_chat_id, group_name)
            VALUES (%s, %s)
            ON CONFLICT (telegram_chat_id)
            DO UPDATE SET group_name = EXCLUDED.group_name
            RETURNING group_id
            """,
            (-1001234567890, "Test chat"),
        ).fetchone()[0]

        conn.execute(
            """
            INSERT INTO group_memberships (group_id, user_id)
            VALUES (%s, %s)
            ON CONFLICT (group_id, user_id) DO NOTHING
            """,
            (group_id, user_id),
        )

        # 2. SELECT with a JOIN across the tables we just filled.
        rows = conn.execute(
            """
            SELECT u.telegram_username, g.group_name, m.status
            FROM group_memberships m
            JOIN users u  USING (user_id)
            JOIN groups g USING (group_id)
            """
        ).fetchall()

    print("Saved and read back:")
    for username, group_name, status in rows:
        print(f"  {username} in '{group_name}' ({status})")

    # 3. The database defends itself: this violates CHECK (telegram_chat_id < 0).
    #    The exception leaves the `with` block, so psycopg rolls the transaction back.
    try:
        with psycopg.connect(DSN) as conn:
            conn.execute(
                "INSERT INTO groups (telegram_chat_id, group_name) VALUES (%s, %s)",
                (123, "private chat id, should be rejected"),
            )
    except errors.CheckViolation as e:
        print("Rejected by the database as expected:", e.diag.message_primary)


if __name__ == "__main__":
    main()
