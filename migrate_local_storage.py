"""Copy a local SQLite database into the private Supabase Postgres schema."""

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3

from psycopg import sql

from database import get_db


TABLES = (
    "users", "sensor_logs", "pain_predictions", "diary_entries", "pain_reports",
    "pain_sos_events", "pain_sos_sensor_samples",
)


def migrate(path):
    if not path.is_file():
        raise ValueError("The SQLite backup does not exist.")
    counts = {}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source, get_db() as target:
        source.execute("BEGIN")
        target.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
            sql.SQL(", ").join(map(sql.Identifier, (*TABLES, "auth_sessions"))),
        ))
        for table in (*TABLES, "auth_sessions"):
            if target.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(table))).fetchone():
                raise ValueError("The target already has data. Import only into a fresh application schema.")
        for table in TABLES:
            rows = source.execute(f"SELECT * FROM {table}")
            columns = [column[0] for column in rows.description]
            if table == "users" and "firebase_user_id" in columns:
                columns[columns.index("firebase_user_id")] = "device_id"
            statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            )
            count = 0
            with target.cursor() as cursor:
                while batch := rows.fetchmany(500):
                    cursor.executemany(statement, batch)
                    count += len(batch)
            counts[table] = count
            if "id" in columns and count:
                target.execute(sql.SQL(
                    "SELECT setval(pg_get_serial_sequence(%s, 'id'), (SELECT MAX(id) FROM {}))"
                ).format(sql.Identifier(table)), (f"neurosync_app.{table}",))
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    for table, count in migrate(args.backup).items():
        print(f"{table}: {count:,} rows copied")
