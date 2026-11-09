import asyncio
import datetime
import json
import os
import sys
from decimal import Decimal

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set in .env")
    sys.exit(1)

OUTPUT_DIR = "backups"
os.makedirs(OUTPUT_DIR, exist_ok=True)
TIMESTAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP_FILE = os.path.join(OUTPUT_DIR, f"supabase_backup_{TIMESTAMP}.json")
SQL_FILE = os.path.join(OUTPUT_DIR, f"supabase_backup_{TIMESTAMP}.sql")


def default_json_serializer(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    return str(obj)


async def run_backup():
    print("Connecting to database...")
    conn = await asyncpg.connect(DATABASE_URL)

    # 1. Fetch tables in public schema
    tables = await conn.fetch("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name;
    """)
    table_names = [t["table_name"] for t in tables]
    print(f"Found {len(table_names)} public tables: {', '.join(table_names)}")

    backup_data = {
        "metadata": {
            "timestamp": datetime.datetime.now().isoformat(),
            "source_host": DATABASE_URL.split("@")[-1],
            "table_count": len(table_names),
        },
        "tables": {},
    }

    sql_statements = [
        "-- Supabase Database Backup: public schema",
        f"-- Date: {datetime.datetime.now().isoformat()}",
        f"-- Source: {DATABASE_URL.split('@')[-1]}",
        "",
        "BEGIN;",
        "",
    ]

    total_rows = 0
    for tbl in table_names:
        cols_info = await conn.fetch(
            """
            SELECT column_name, data_type, udt_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            ORDER BY ordinal_position;
        """,
            tbl,
        )

        col_names = [c["column_name"] for c in cols_info]

        rows = await conn.fetch(f'SELECT * FROM "public"."{tbl}"')
        row_dicts = [dict(r) for r in rows]

        print(f"Backing up '{tbl}': {len(row_dicts)} rows")
        total_rows += len(row_dicts)

        backup_data["tables"][tbl] = {
            "columns": [dict(c) for c in cols_info],
            "row_count": len(row_dicts),
            "rows": row_dicts,
        }

        # Generate SQL Inserts
        if row_dicts:
            cols_quoted = [f'"{col}"' for col in col_names]
            cols_clause = ", ".join(cols_quoted)
            for r in row_dicts:
                values_escaped = []
                for col in col_names:
                    val = r.get(col)
                    if val is None:
                        values_escaped.append("NULL")
                    elif isinstance(val, (int, float, Decimal)):
                        values_escaped.append(str(val))
                    elif isinstance(val, bool):
                        values_escaped.append("TRUE" if val else "FALSE")
                    elif isinstance(val, (dict, list)):
                        json_str = json.dumps(val, default=default_json_serializer).replace("'", "''")
                        values_escaped.append(f"'{json_str}'")
                    elif isinstance(val, (datetime.date, datetime.datetime)):
                        values_escaped.append(f"'{val.isoformat()}'")
                    else:
                        str_val = str(val).replace("'", "''")
                        values_escaped.append(f"'{str_val}'")

                sql_statements.append(
                    f'INSERT INTO "public"."{tbl}" ({cols_clause}) VALUES ({", ".join(values_escaped)});'
                )

    sql_statements.append("")
    sql_statements.append("COMMIT;")

    # Save JSON backup
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(backup_data, f, default=default_json_serializer, indent=2)

    # Save SQL backup
    with open(SQL_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sql_statements))

    await conn.close()

    print("\n--- BACKUP COMPLETED SUCCESSFULLY ---")
    print(f"Total Rows Saved: {total_rows}")
    print(f"JSON Backup File: {BACKUP_FILE} ({os.path.getsize(BACKUP_FILE) / 1024 / 1024:.2f} MB)")
    print(f"SQL Dump File:    {SQL_FILE} ({os.path.getsize(SQL_FILE) / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    asyncio.run(run_backup())
