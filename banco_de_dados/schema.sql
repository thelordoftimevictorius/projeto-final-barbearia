CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'employee', 'client', 'business_owner')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS barbershops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
    business_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT NOT NULL,
    address TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    location_is_approximate INTEGER NOT NULL DEFAULT 0
        CHECK (location_is_approximate IN (0, 1)),
    image_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT,
    birth_date TEXT,
    profile_photo_url TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS barbers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER REFERENCES barbershops (id) ON DELETE RESTRICT,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    specialty TEXT,
    profile_photo_url TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER REFERENCES barbershops (id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    description TEXT,
    image_url TEXT,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER REFERENCES barbershops (id) ON DELETE RESTRICT,
    client_id INTEGER NOT NULL REFERENCES clients (id) ON DELETE RESTRICT,
    barber_id INTEGER NOT NULL REFERENCES barbers (id) ON DELETE RESTRICT,
    service_id INTEGER NOT NULL REFERENCES services (id) ON DELETE RESTRICT,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (
            status IN (
                'scheduled',
                'confirmed',
                'completed',
                'cancelled',
                'no_show'
            )
        ),
    notes TEXT,
    created_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (ends_at > starts_at)
);

CREATE TABLE IF NOT EXISTS service_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL REFERENCES services (id) ON DELETE CASCADE,
    client_id INTEGER NOT NULL REFERENCES clients (id) ON DELETE CASCADE,
    month_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (service_id, client_id, month_key)
);

CREATE INDEX IF NOT EXISTS idx_appointments_starts_at
    ON appointments (starts_at);
CREATE INDEX IF NOT EXISTS idx_appointments_barber_interval
    ON appointments (barber_id, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_appointments_client
    ON appointments (client_id);
CREATE TRIGGER IF NOT EXISTS prevent_appointment_overlap_on_insert
BEFORE INSERT ON appointments
WHEN NEW.status != 'cancelled'
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM appointments AS existing
        WHERE existing.barber_id = NEW.barber_id
          AND existing.status != 'cancelled'
          AND existing.starts_at < NEW.ends_at
          AND existing.ends_at > NEW.starts_at
    )
    THEN RAISE(ABORT, 'appointment_conflict') END;
END;

CREATE TRIGGER IF NOT EXISTS prevent_appointment_overlap_on_update
BEFORE UPDATE OF barber_id, starts_at, ends_at, status ON appointments
WHEN NEW.status != 'cancelled'
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM appointments AS existing
        WHERE existing.id != NEW.id
          AND existing.barber_id = NEW.barber_id
          AND existing.status != 'cancelled'
          AND existing.starts_at < NEW.ends_at
          AND existing.ends_at > NEW.starts_at
    )
    THEN RAISE(ABORT, 'appointment_conflict') END;
END;
