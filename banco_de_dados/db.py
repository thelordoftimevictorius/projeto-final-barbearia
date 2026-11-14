import sqlite3
from pathlib import Path

from flask import current_app, g
from werkzeug.security import generate_password_hash


def get_db():
    if "db" not in g:
        database_path = Path(current_app.config["DATABASE"])
        database_path.parent.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection

    return g.db


def close_db(error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    database = get_db()
    schema_path = Path(__file__).with_name("schema.sql")
    schema = schema_path.read_text(encoding="utf-8")
    database.executescript(schema)
    ensure_user_role_support()
    ensure_barbershop_columns()
    ensure_client_profile_photo_column()
    ensure_barber_profile_photo_column()
    ensure_client_user_id_column()
    ensure_appointments_fk_health()
    database.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_barbers_shop_status
            ON barbers (shop_id, status);
        CREATE INDEX IF NOT EXISTS idx_services_shop_status
            ON services (shop_id, status);
        CREATE INDEX IF NOT EXISTS idx_appointments_shop_starts_at
            ON appointments (shop_id, starts_at);
        """
    )
    database.commit()


def ensure_user_role_support():
    database = get_db()
    legacy_table = database.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()
    if legacy_table is None:
        return

    existing_sql = (legacy_table["sql"] or "").lower()
    if "business_owner" in existing_sql:
        return

    columns = database.execute("PRAGMA table_info(users)").fetchall()
    if not columns:
        return

    column_names = [row[1] for row in columns]
    if "role" not in column_names:
        return

    supported_columns = [
        name for name in (
            "id",
            "full_name",
            "email",
            "password_hash",
            "role",
            "is_active",
            "created_at",
            "updated_at",
        )
        if name in column_names
    ]
    columns_sql = ", ".join(supported_columns)
    database.execute("PRAGMA foreign_keys = OFF")
    try:
        database.executescript(
            f"""
        BEGIN;
        CREATE TABLE users_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (
                role IN ('admin', 'employee', 'client', 'business_owner')
            ),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO users_new ({columns_sql})
            SELECT {columns_sql} FROM users;
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
        COMMIT;
        """
        )
    except sqlite3.Error:
        database.rollback()
        raise
    finally:
        database.execute("PRAGMA foreign_keys = ON")


def ensure_barbershop_columns():
    database = get_db()
    columns_by_table = {
        "barbers": {
            "shop_id": "INTEGER REFERENCES barbershops(id) ON DELETE RESTRICT",
        },
        "services": {
            "shop_id": "INTEGER REFERENCES barbershops(id) ON DELETE RESTRICT",
            "image_url": "TEXT",
        },
        "appointments": {
            "shop_id": "INTEGER REFERENCES barbershops(id) ON DELETE RESTRICT",
        },
    }

    for table_name, columns in columns_by_table.items():
        existing = {
            row[1] for row in database.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()
        }
        for column_name, definition in columns.items():
            if column_name not in existing:
                database.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
                )


def ensure_client_profile_photo_column():
    database = get_db()
    columns = database.execute(
        "PRAGMA table_info(clients)"
    ).fetchall()
    column_names = {row[1] for row in columns}
    if "profile_photo_url" not in column_names:
        database.execute(
            "ALTER TABLE clients ADD COLUMN profile_photo_url TEXT"
        )


def ensure_barber_profile_photo_column():
    database = get_db()
    columns = database.execute(
        "PRAGMA table_info(barbers)"
    ).fetchall()
    column_names = {row[1] for row in columns}
    if "profile_photo_url" not in column_names:
        database.execute(
            "ALTER TABLE barbers ADD COLUMN profile_photo_url TEXT"
        )


def ensure_client_user_id_column():
    database = get_db()
    columns = database.execute("PRAGMA table_info(clients)").fetchall()
    column_names = {row[1] for row in columns}
    if "user_id" not in column_names:
        database.execute(
            "ALTER TABLE clients ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL"
        )

    database.execute(
        """
        UPDATE clients
        SET user_id = (
            SELECT users.id
            FROM users
            WHERE users.email = clients.email COLLATE NOCASE
              AND users.role = 'client'
            ORDER BY users.id
            LIMIT 1
        )
        WHERE user_id IS NULL
          AND email IS NOT NULL
          AND id = (
              SELECT MIN(other_client.id)
              FROM clients AS other_client
              WHERE other_client.email = clients.email COLLATE NOCASE
          )
        """
    )
    database.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_user_id
        ON clients (user_id)
        WHERE user_id IS NOT NULL
        """
    )


def ensure_appointments_fk_health():
    database = get_db()
    table = database.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'appointments'"
    ).fetchone()
    if table and "users_legacy" in (table["sql"] or ""):
        database.execute("PRAGMA foreign_keys = OFF")
        try:
            database.executescript(
                """
                BEGIN TRANSACTION;
                CREATE TABLE appointments_fixed (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shop_id INTEGER REFERENCES barbershops (id) ON DELETE RESTRICT,
                    client_id INTEGER NOT NULL REFERENCES clients (id) ON DELETE RESTRICT,
                    barber_id INTEGER NOT NULL REFERENCES barbers (id) ON DELETE RESTRICT,
                    service_id INTEGER NOT NULL REFERENCES services (id) ON DELETE RESTRICT,
                    starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
                    status TEXT NOT NULL DEFAULT 'scheduled'
                        CHECK (status IN ('scheduled', 'confirmed', 'completed', 'cancelled', 'no_show')),
                    notes TEXT,
                    created_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK (ends_at > starts_at)
                );
                INSERT INTO appointments_fixed (
                    id, shop_id, client_id, barber_id, service_id, starts_at, ends_at, price_cents, status, notes, created_by, created_at, updated_at
                )
                SELECT id, shop_id, client_id, barber_id, service_id, starts_at, ends_at, price_cents, status, notes, created_by, created_at, updated_at
                FROM appointments;
                DROP TABLE appointments;
                ALTER TABLE appointments_fixed RENAME TO appointments;
                COMMIT;
                """
            )
        finally:
            database.execute("PRAGMA foreign_keys = ON")


def create_default_admin():
    database = get_db()
    user_exists = database.execute(
        "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE",
        ("admin@barber.local",),
    ).fetchone()

    if user_exists is None:
        database.execute(
            """
            INSERT INTO users (full_name, email, password_hash, role, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (
                "Administrador",
                "admin@barber.local",
                generate_password_hash("admin123"),
                "admin",
            ),
        )
        database.commit()


def create_default_employee():
    database = get_db()
    user_exists = database.execute(
        "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE",
        ("funcionario@barber.local",),
    ).fetchone()

    if user_exists is None:
        database.execute(
            """
            INSERT INTO users (full_name, email, password_hash, role, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (
                "Funcionário",
                "funcionario@barber.local",
                generate_password_hash("funcionario123"),
                "employee",
            ),
        )
        database.commit()


def create_default_client():
    database = get_db()
    user_exists = database.execute(
        "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE",
        ("cliente@barber.local",),
    ).fetchone()

    if user_exists is None:
        database.execute(
            """
            INSERT INTO users (full_name, email, password_hash, role, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (
                "Cliente",
                "cliente@barber.local",
                generate_password_hash("cliente123"),
                "client",
            ),
        )
        database.commit()

    client_exists = database.execute(
        "SELECT 1 FROM clients WHERE email = ? COLLATE NOCASE",
        ("cliente@barber.local",),
    ).fetchone()

    if client_exists is None:
        user = database.execute(
            "SELECT id FROM users WHERE email = ? COLLATE NOCASE",
            ("cliente@barber.local",),
        ).fetchone()
        database.execute(
            """
            INSERT INTO clients (full_name, phone, email, profile_photo_url, user_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Cliente",
                "11900000000",
                "cliente@barber.local",
                "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?auto=format&fit=crop&w=400&q=80",
                user["id"] if user else None,
            ),
        )
        database.commit()


def create_default_users():
    create_default_admin()
    create_default_employee()
    create_default_client()
