import sqlite3
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import generate_password_hash

from banco_de_dados.db import get_db
from modelos.user import authenticate_user


main_bp = Blueprint("main", __name__)
APPOINTMENT_STATUSES = ["scheduled", "confirmed", "completed", "cancelled", "no_show"]
BOOKING_OPEN_MINUTE = 5 * 60
BOOKING_CLOSE_MINUTE = 24 * 60
BOOKING_SLOT_MINUTES = 15


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        if current_user.role not in ("admin", "business_owner"):
            return redirect(url_for("main.dashboard"))
        return view(*args, **kwargs)

    return wrapped_view


def admin_only(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        if current_user.role != "admin":
            return redirect(url_for("main.dashboard"))
        return view(*args, **kwargs)

    return wrapped_view


def current_business_id():
    if not current_user.is_authenticated or current_user.role != "business_owner":
        return None

    shop = get_db().execute(
        "SELECT id FROM barbershops WHERE owner_user_id = ?",
        (current_user.id,),
    ).fetchone()
    return shop["id"] if shop else None


def shop_scope(column, alias=None):
    qualified_column = f"{alias}.{column}" if alias else column
    if current_user.role == "business_owner":
        return f" AND {qualified_column} = ?", [current_business_id()]
    if current_user.role == "employee":
        return f" AND {qualified_column} IS NULL", []
    return "", []


def valid_image_url(value):
    image_url = (value or "").strip()
    if not image_url:
        return None
    return image_url if image_url.startswith("https://") else None


def save_service_photo(upload, image_url_value, existing_image_url=None):
    if upload and upload.filename:
        extension = Path(upload.filename).suffix.lower()
        signatures = {
            ".jpg": lambda data: data.startswith(b"\xff\xd8\xff"),
            ".jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
            ".png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
            ".webp": (
                lambda data: data.startswith(b"RIFF") and data[8:12] == b"WEBP"
            ),
        }
        image_signature = signatures.get(extension)
        if image_signature is None:
            return None, "Envie uma imagem JPG, PNG ou WEBP."

        header = upload.stream.read(12)
        upload.stream.seek(0)
        if not image_signature(header):
            return None, "O arquivo enviado não corresponde a uma imagem JPG, PNG ou WEBP válida."

        filename = f"{uuid.uuid4().hex}{extension}"
        upload_directory = Path(current_app.instance_path) / "uploads" / "catalogo"
        upload_directory.mkdir(parents=True, exist_ok=True)
        upload.save(upload_directory / filename)
        return url_for("main.catalog_image", filename=filename), None

    image_url = valid_image_url(image_url_value)
    if image_url_value.strip() and image_url is None:
        return None, "Informe uma URL HTTPS válida ou envie uma imagem JPG, PNG ou WEBP."
    return image_url or existing_image_url, None


def parse_int(value, minimum=None, default=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default

    if minimum is not None and parsed < minimum:
        return default
    return parsed


def get_client_profile_for_current_user():
    if not current_user.is_authenticated or current_user.role != "client":
        return None

    db = get_db()
    return db.execute(
        """
        SELECT *
        FROM clients
        WHERE user_id = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (current_user.id,),
    ).fetchone()


@main_bp.get("/")
def home():
    return render_template("index.html", current_user=current_user)


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        user = authenticate_user(email, password)

        if user is not None:
            login_user(user)
            return redirect(url_for("main.dashboard"))

        return render_template("login.html", error="E-mail ou senha inválidos.")

    return render_template("login.html")


@main_bp.route("/cadastro/cliente", methods=["GET", "POST"])
def register_client():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        phone = (request.form.get("phone") or "").strip()
        password = request.form.get("password") or ""
        password_confirmation = request.form.get("password_confirmation") or ""

        if not full_name or not email or not phone:
            return render_template(
                "client_register.html",
                error="Preencha nome, e-mail e telefone.",
                form=request.form,
            )
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            return render_template(
                "client_register.html",
                error="Informe um e-mail válido.",
                form=request.form,
            )
        if len(password) < 8:
            return render_template(
                "client_register.html",
                error="A senha precisa ter pelo menos 8 caracteres.",
                form=request.form,
            )
        if password != password_confirmation:
            return render_template(
                "client_register.html",
                error="As senhas não conferem.",
                form=request.form,
            )

        db = get_db()
        if db.execute(
            "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE",
            (email,),
        ).fetchone():
            return render_template(
                "client_register.html",
                error="Já existe uma conta com este e-mail.",
                form=request.form,
            )

        try:
            user_cursor = db.execute(
                """
                INSERT INTO users (full_name, email, password_hash, role, is_active)
                VALUES (?, ?, ?, 'client', 1)
                """,
                (full_name, email, generate_password_hash(password)),
            )
            user_id = user_cursor.lastrowid
            existing_profile = db.execute(
                "SELECT id, user_id FROM clients WHERE email = ? COLLATE NOCASE LIMIT 1",
                (email,),
            ).fetchone()

            if existing_profile and existing_profile["user_id"] is None:
                db.execute(
                    """
                    UPDATE clients
                    SET full_name = ?, phone = ?, user_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (full_name, phone, user_id, existing_profile["id"]),
                )
            else:
                db.execute(
                    """
                    INSERT INTO clients (full_name, phone, email, user_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (full_name, phone, email, user_id),
                )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            return render_template(
                "client_register.html",
                error="Não foi possível criar a conta. Verifique se o e-mail já está cadastrado.",
                form=request.form,
            )

        from modelos.user import get_user_by_id

        login_user(get_user_by_id(user_id))
        flash("Conta criada. Agora você já pode agendar seu atendimento.", "success")
        return redirect(url_for("main.appointments"))

    return render_template("client_register.html", error=None, form={})


@main_bp.route("/cadastro/barbearia", methods=["GET", "POST"])
def register_barbershop():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        business_name = (request.form.get("business_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        phone = (request.form.get("phone") or "").strip()
        address = (request.form.get("address") or "").strip()
        password = request.form.get("password") or ""
        password_confirmation = request.form.get("password_confirmation") or ""
        latitude = request.form.get("latitude", type=float)
        longitude = request.form.get("longitude", type=float)
        location_is_approximate = int(
            request.form.get("location_is_approximate") == "on"
        )

        if not full_name or not business_name or not email or not phone or not address:
            return render_template(
                "cadastro_barbearia.html",
                error="Preencha os dados do responsável, contato e endereço.",
                form=request.form,
            )
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            return render_template(
                "cadastro_barbearia.html",
                error="Informe um e-mail válido.",
                form=request.form,
            )
        if len(password) < 8:
            return render_template(
                "cadastro_barbearia.html",
                error="A senha precisa ter pelo menos 8 caracteres.",
                form=request.form,
            )
        if password != password_confirmation:
            return render_template(
                "cadastro_barbearia.html",
                error="As senhas não conferem.",
                form=request.form,
            )
        if (
            latitude is None
            or longitude is None
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            return render_template(
                "cadastro_barbearia.html",
                error="Informe coordenadas válidas para marcar a localização.",
                form=request.form,
            )

        db = get_db()
        if db.execute(
            "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE",
            (email,),
        ).fetchone():
            return render_template(
                "cadastro_barbearia.html",
                error="Já existe uma conta com este e-mail.",
                form=request.form,
            )

        try:
            user_cursor = db.execute(
                """
                INSERT INTO users (full_name, email, password_hash, role, is_active)
                VALUES (?, ?, ?, 'business_owner', 1)
                """,
                (full_name, email, generate_password_hash(password)),
            )
            user_id = user_cursor.lastrowid
            shop_cursor = db.execute(
                """
                INSERT INTO barbershops (
                    owner_user_id, business_name, phone, email, address,
                    latitude, longitude, location_is_approximate
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    business_name,
                    phone,
                    email,
                    address,
                    latitude,
                    longitude,
                    location_is_approximate,
                ),
            )
            shop_id = shop_cursor.lastrowid
            sample_services = (
                (
                    "Corte tradicional",
                    "Corte clássico com acabamento cuidadoso.",
                    3500,
                    30,
                    "https://images.unsplash.com/photo-1503951914875-452162b0f3f1?auto=format&fit=crop&w=900&q=80",
                ),
                (
                    "Degradê",
                    "Degradê na altura e no estilo que você escolher.",
                    4500,
                    45,
                    "https://images.unsplash.com/photo-1621605815971-fbc98d665033?auto=format&fit=crop&w=900&q=80",
                ),
                (
                    "Barba",
                    "Aparar, alinhar e finalizar a barba.",
                    2500,
                    30,
                    "https://images.unsplash.com/photo-1599351431202-1e0f0137899a?auto=format&fit=crop&w=900&q=80",
                ),
                (
                    "Corte + barba",
                    "Corte tradicional acompanhado de barba.",
                    6000,
                    60,
                    "https://images.unsplash.com/photo-1622286342621-4bd786c2447c?auto=format&fit=crop&w=900&q=80",
                ),
            )
            db.executemany(
                """
                INSERT INTO services
                    (shop_id, name, description, image_url, price_cents, duration_minutes, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')
                """,
                [
                    (shop_id, name, description, image_url, price, duration)
                    for name, description, price, duration, image_url in sample_services
                ],
            )
            db.execute(
                """
                INSERT INTO barbers (shop_id, full_name, phone, specialty, status)
                VALUES (?, ?, ?, ?, 'active')
                """,
                (
                    shop_id,
                    "Profissional de demonstração",
                    phone,
                    "Substitua pelos dados da equipe",
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            return render_template(
                "cadastro_barbearia.html",
                error="Não foi possível criar a conta. Verifique se o e-mail já está cadastrado.",
                form=request.form,
            )

        from modelos.user import get_user_by_id

        login_user(get_user_by_id(user_id))
        flash(
            "Barbearia cadastrada. Revise os serviços e substitua o profissional de demonstração pela sua equipe.",
            "success",
        )
        return redirect(url_for("main.dashboard"))

    return render_template("cadastro_barbearia.html", error=None, form={})


@main_bp.route("/barbearia/perfil", methods=["GET", "POST"])
@login_required
def business_profile():
    if current_user.role != "business_owner":
        return redirect(url_for("main.dashboard"))

    db = get_db()
    shop = db.execute(
        "SELECT * FROM barbershops WHERE owner_user_id = ?",
        (current_user.id,),
    ).fetchone()
    if shop is None:
        flash("O perfil da barbearia não foi encontrado.", "danger")
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        business_name = (request.form.get("business_name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        address = (request.form.get("address") or "").strip()
        latitude = request.form.get("latitude", type=float)
        longitude = request.form.get("longitude", type=float)
        image_url = valid_image_url(request.form.get("image_url"))
        location_is_approximate = int(
            request.form.get("location_is_approximate") == "on"
        )
        image_url_value = request.form.get("image_url") or ""
        if (
            not business_name
            or not phone
            or not address
            or latitude is None
            or longitude is None
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
            or (image_url_value.strip() and image_url is None)
        ):
            return render_template(
                "perfil_barbearia.html",
                shop=dict(shop),
                error="Preencha nome, telefone, endereço e coordenadas válidas.",
                current_user=current_user,
            )

        db.execute(
            """
            UPDATE barbershops
            SET business_name = ?, phone = ?, address = ?, latitude = ?,
                longitude = ?, location_is_approximate = ?, image_url = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND owner_user_id = ?
            """,
            (
                business_name,
                phone,
                address,
                latitude,
                longitude,
                location_is_approximate,
                image_url,
                shop["id"],
                current_user.id,
            ),
        )
        db.commit()
        flash("Perfil da barbearia atualizado.", "success")
        return redirect(url_for("main.business_profile"))

    return render_template(
        "perfil_barbearia.html",
        shop=dict(shop),
        error=None,
        current_user=current_user,
    )


@main_bp.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.login"))


@main_bp.get("/barbearias")
def browse_barbershops():
    shops = get_db().execute(
        "SELECT * FROM barbershops ORDER BY business_name COLLATE NOCASE"
    ).fetchall()
    return render_template(
        "barbearias.html",
        shops=shops,
        current_user=current_user,
    )


@main_bp.get("/catalogo/imagens/<filename>")
def catalog_image(filename):
    extension = Path(filename).suffix.lower()
    mimetypes = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    mimetype = mimetypes.get(extension)
    if mimetype is None:
        return "", 404

    response = send_from_directory(
        Path(current_app.instance_path) / "uploads" / "catalogo",
        filename,
        mimetype=mimetype,
        max_age=86400,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@main_bp.get("/dashboard")
@login_required
def dashboard():
    if current_user.role == "client":
        db = get_db()
        client_profile = get_client_profile_for_current_user()
        if client_profile is None:
            client_profile = {
                "id": None,
                "full_name": current_user.full_name,
                "email": current_user.email,
                "profile_photo_url": None,
            }

        appointments = db.execute(
            """
            SELECT a.*, c.full_name AS client_name, c.profile_photo_url AS client_photo_url,
                   b.full_name AS barber_name, s.name AS service_name, s.price_cents AS service_price_cents
            FROM appointments AS a
            JOIN clients AS c ON c.id = a.client_id
            JOIN barbers AS b ON b.id = a.barber_id
            JOIN services AS s ON s.id = a.service_id
            WHERE a.client_id = ?
            ORDER BY a.starts_at ASC
            """,
            (client_profile["id"],),
        ).fetchall() if client_profile["id"] is not None else []
        return render_template(
            "client_dashboard.html",
            current_user=current_user,
            client_profile=dict(client_profile),
            appointments=appointments,
        )

    db = get_db()
    if current_user.role == "business_owner":
        shop = db.execute(
            "SELECT * FROM barbershops WHERE owner_user_id = ?",
            (current_user.id,),
        ).fetchone()
        if shop is None:
            flash("O perfil da barbearia não foi encontrado.", "danger")
            return redirect(url_for("main.business_profile"))

        shop_id = shop["id"]
        counts = {
            "clients": db.execute(
                """
                SELECT COUNT(DISTINCT client_id) FROM appointments
                WHERE shop_id = ?
                """,
                (shop_id,),
            ).fetchone()[0],
            "barbers": db.execute(
                "SELECT COUNT(*) FROM barbers WHERE shop_id = ?",
                (shop_id,),
            ).fetchone()[0],
            "services": db.execute(
                "SELECT COUNT(*) FROM services WHERE shop_id = ?",
                (shop_id,),
            ).fetchone()[0],
            "appointments": db.execute(
                "SELECT COUNT(*) FROM appointments WHERE shop_id = ?",
                (shop_id,),
            ).fetchone()[0],
        }
        recent_appointments = db.execute(
            """
            SELECT a.*, c.full_name AS client_name, b.full_name AS barber_name,
                   s.name AS service_name
            FROM appointments AS a
            JOIN clients AS c ON c.id = a.client_id
            JOIN barbers AS b ON b.id = a.barber_id
            JOIN services AS s ON s.id = a.service_id
            WHERE a.shop_id = ?
            ORDER BY a.starts_at DESC
            LIMIT 8
            """,
            (shop_id,),
        ).fetchall()
        return render_template(
            "painel_barbearia.html",
            current_user=current_user,
            shop=dict(shop),
            counts=counts,
            appointments=recent_appointments,
        )

    clients_count = db.execute("SELECT COUNT(*) AS total FROM clients").fetchone()["total"]
    barbers_count = db.execute("SELECT COUNT(*) AS total FROM barbers").fetchone()["total"]
    services_count = db.execute("SELECT COUNT(*) AS total FROM services").fetchone()["total"]
    appointments_count = db.execute(
        "SELECT COUNT(*) AS total FROM appointments"
    ).fetchone()["total"]
    today_count = db.execute(
        "SELECT COUNT(*) AS total FROM appointments WHERE date(starts_at) = date('now', 'localtime')"
    ).fetchone()["total"]
    scheduled_count = db.execute(
        "SELECT COUNT(*) AS total FROM appointments WHERE status = 'scheduled'"
    ).fetchone()["total"]
    confirmed_count = db.execute(
        "SELECT COUNT(*) AS total FROM appointments WHERE status = 'confirmed'"
    ).fetchone()["total"]
    revenue_total = db.execute(
        "SELECT COALESCE(SUM(price_cents), 0) AS total FROM appointments WHERE status != 'cancelled'"
    ).fetchone()["total"]
    todays_appointments = db.execute(
        """
        SELECT a.*, c.full_name AS client_name, b.full_name AS barber_name, s.name AS service_name
        FROM appointments AS a
        JOIN clients AS c ON c.id = a.client_id
        JOIN barbers AS b ON b.id = a.barber_id
        JOIN services AS s ON s.id = a.service_id
        WHERE date(a.starts_at) = date('now', 'localtime')
        ORDER BY a.starts_at ASC
        """
    ).fetchall()

    current_month = date.today().strftime("%Y-%m")
    top_services_this_month = db.execute(
        """
        SELECT s.name,
               COUNT(v.id) AS vote_count
        FROM services AS s
        LEFT JOIN service_votes AS v ON v.service_id = s.id AND v.month_key = ?
        GROUP BY s.id, s.name
        ORDER BY vote_count DESC, s.name ASC
        LIMIT 10
        """,
        (current_month,),
    ).fetchall()
    max_monthly_votes = max((row["vote_count"] for row in top_services_this_month), default=0)

    start_of_week = date.today() - timedelta(days=date.today().weekday())
    weekly_summary = db.execute(
        """
        SELECT COUNT(*) AS appointments_count,
               COALESCE(SUM(CASE WHEN status != 'cancelled' THEN price_cents ELSE 0 END), 0) AS revenue_total
        FROM appointments
        WHERE date(starts_at) BETWEEN ? AND date('now', 'localtime')
        """,
        (start_of_week.isoformat(),),
    ).fetchone()

    chart_days = []
    chart_start = date.today() - timedelta(days=6)
    chart_rows = db.execute(
        """
        SELECT date(starts_at) AS day,
               COALESCE(SUM(CASE WHEN status != 'cancelled' THEN price_cents ELSE 0 END), 0) AS revenue_total
        FROM appointments
        WHERE date(starts_at) BETWEEN ? AND date('now', 'localtime')
        GROUP BY date(starts_at)
        """,
        (chart_start.isoformat(),),
    ).fetchall()
    day_totals = {row["day"]: row["revenue_total"] for row in chart_rows}
    for offset in range(7):
        current = chart_start + timedelta(days=offset)
        chart_days.append({
            "day": current.strftime("%a"),
            "date": current.isoformat(),
            "revenue_total": day_totals.get(current.isoformat(), 0),
        })
    max_chart_value = max((item["revenue_total"] for item in chart_days), default=0)

    top_barbers_this_month = db.execute(
        """
        SELECT b.full_name,
               COUNT(a.id) AS appointments_count,
               COALESCE(SUM(CASE WHEN a.status != 'cancelled' THEN a.price_cents ELSE 0 END), 0) AS revenue_total
        FROM barbers AS b
        LEFT JOIN appointments AS a ON a.barber_id = b.id
          AND strftime('%Y-%m', a.starts_at) = ?
        GROUP BY b.id, b.full_name
        ORDER BY appointments_count DESC, revenue_total DESC, b.full_name ASC
        LIMIT 5
        """,
        (current_month,),
    ).fetchall()
    max_barber_monthly_revenue = max((row["revenue_total"] for row in top_barbers_this_month), default=0)

    return render_template(
        "dashboard.html",
        current_user=current_user,
        clients_count=clients_count,
        barbers_count=barbers_count,
        services_count=services_count,
        appointments_count=appointments_count,
        today_count=today_count,
        scheduled_count=scheduled_count,
        confirmed_count=confirmed_count,
        revenue_total=revenue_total,
        todays_appointments=todays_appointments,
        top_services_this_month=top_services_this_month,
        max_monthly_votes=max_monthly_votes,
        weekly_summary=weekly_summary,
        chart_days=chart_days,
        max_chart_value=max_chart_value,
        top_barbers_this_month=top_barbers_this_month,
        max_barber_monthly_revenue=max_barber_monthly_revenue,
    )


@main_bp.get("/reports")
@login_required
def reports():
    if current_user.role in ("client", "business_owner"):
        return redirect(url_for("main.dashboard"))

    db = get_db()
    start_date = request.args.get("start_date") or (date.today() - timedelta(days=7)).isoformat()
    end_date = request.args.get("end_date") or date.today().isoformat()
    barber_id = parse_int(request.args.get("barber_id"), minimum=1, default=None)

    base_query = """
        SELECT a.*, c.full_name AS client_name, b.full_name AS barber_name, s.name AS service_name
        FROM appointments AS a
        JOIN clients AS c ON c.id = a.client_id
        JOIN barbers AS b ON b.id = a.barber_id
        JOIN services AS s ON s.id = a.service_id
        WHERE date(a.starts_at) BETWEEN ? AND ?
    """
    params = [start_date, end_date]
    if barber_id is not None:
        base_query += " AND a.barber_id = ?"
        params.append(barber_id)
    base_query += " ORDER BY a.starts_at ASC"

    rows = db.execute(base_query, params).fetchall()
    summary = db.execute(
        """
        SELECT COUNT(*) AS total_appointments,
               COALESCE(SUM(CASE WHEN status != 'cancelled' THEN price_cents ELSE 0 END), 0) AS revenue_total,
               COALESCE(SUM(CASE WHEN status = 'scheduled' THEN 1 ELSE 0 END), 0) AS scheduled_total,
               COALESCE(SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END), 0) AS confirmed_total
        FROM appointments
        WHERE date(starts_at) BETWEEN ? AND ?
        """,
        [start_date, end_date],
    ).fetchone()
    if barber_id is not None:
        filtered_summary = db.execute(
            """
            SELECT COUNT(*) AS total_appointments,
                   COALESCE(SUM(CASE WHEN status != 'cancelled' THEN price_cents ELSE 0 END), 0) AS revenue_total,
                   COALESCE(SUM(CASE WHEN status = 'scheduled' THEN 1 ELSE 0 END), 0) AS scheduled_total,
                   COALESCE(SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END), 0) AS confirmed_total
            FROM appointments
            WHERE date(starts_at) BETWEEN ? AND ?
              AND barber_id = ?
            """,
            [start_date, end_date, barber_id],
        ).fetchone()
        summary = filtered_summary

    barbers = db.execute("SELECT id, full_name FROM barbers ORDER BY full_name ASC").fetchall()
    clients = db.execute("SELECT id, full_name FROM clients ORDER BY full_name ASC").fetchall()
    services = db.execute("SELECT id, name FROM services ORDER BY name ASC").fetchall()

    daily_summary_query = """
        SELECT date(a.starts_at) AS day,
               COUNT(*) AS total_appointments,
               COALESCE(SUM(CASE WHEN a.status != 'cancelled' THEN a.price_cents ELSE 0 END), 0) AS revenue_total
        FROM appointments AS a
        WHERE date(a.starts_at) BETWEEN ? AND ?
    """
    daily_summary_params = [start_date, end_date]
    if barber_id is not None:
        daily_summary_query += " AND a.barber_id = ?"
        daily_summary_params.append(barber_id)
    daily_summary_query += " GROUP BY date(a.starts_at) ORDER BY date(a.starts_at) ASC"
    daily_summary = db.execute(daily_summary_query, daily_summary_params).fetchall()

    barber_summary = db.execute(
        """
        SELECT b.full_name,
               COUNT(a.id) AS appointment_count,
               COALESCE(SUM(CASE WHEN a.status != 'cancelled' THEN a.price_cents ELSE 0 END), 0) AS revenue_total
        FROM barbers AS b
        LEFT JOIN appointments AS a ON a.barber_id = b.id
          AND date(a.starts_at) BETWEEN ? AND ?
        WHERE (? IS NULL OR b.id = ?)
        GROUP BY b.id, b.full_name
        ORDER BY revenue_total DESC, b.full_name ASC
        """,
        [start_date, end_date, barber_id, barber_id],
    ).fetchall()

    service_summary = db.execute(
        """
        SELECT s.name,
               COUNT(a.id) AS appointment_count,
               COALESCE(SUM(CASE WHEN a.status != 'cancelled' THEN a.price_cents ELSE 0 END), 0) AS revenue_total
        FROM services AS s
        LEFT JOIN appointments AS a ON a.service_id = s.id
          AND date(a.starts_at) BETWEEN ? AND ?
        WHERE (? IS NULL OR a.barber_id = ?)
        GROUP BY s.id, s.name
        ORDER BY revenue_total DESC, s.name ASC
        """,
        [start_date, end_date, barber_id, barber_id],
    ).fetchall()

    current_month = date.today().strftime("%Y-%m")
    top_services_this_month = db.execute(
        """
        SELECT s.id,
               s.name,
               COUNT(v.id) AS vote_count
        FROM services AS s
        LEFT JOIN service_votes AS v ON v.service_id = s.id AND v.month_key = ?
        GROUP BY s.id, s.name
        ORDER BY vote_count DESC, s.name ASC
        LIMIT 10
        """,
        (current_month,),
    ).fetchall()

    max_barber_revenue = max((row["revenue_total"] for row in barber_summary), default=0)
    max_service_revenue = max((row["revenue_total"] for row in service_summary), default=0)
    max_monthly_votes = max((row["vote_count"] for row in top_services_this_month), default=0)

    return render_template(
        "reports.html",
        current_user=current_user,
        reports=rows,
        barbers=barbers,
        clients=clients,
        services=services,
        start_date=start_date,
        end_date=end_date,
        selected_barber_id=barber_id,
        total_appointments=summary["total_appointments"],
        revenue_total=summary["revenue_total"],
        scheduled_total=summary["scheduled_total"],
        confirmed_total=summary["confirmed_total"],
        daily_summary=daily_summary,
        barber_summary=barber_summary,
        service_summary=service_summary,
        max_barber_revenue=max_barber_revenue,
        max_service_revenue=max_service_revenue,
        top_services_this_month=top_services_this_month,
        max_monthly_votes=max_monthly_votes,
    )


@main_bp.get("/users")
@admin_only
def users():
    rows = get_db().execute(
        "SELECT * FROM users ORDER BY full_name ASC"
    ).fetchall()
    return render_template("users.html", users=rows, current_user=current_user)


@main_bp.post("/users/<int:user_id>/role")
@admin_only
def update_user_role(user_id):
    if user_id == current_user.id:
        return redirect(url_for("main.users"))

    role = request.form.get("role") or "employee"
    if role not in {"admin", "employee", "client", "business_owner"}:
        role = "employee"

    get_db().execute(
        "UPDATE users SET role = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (role, user_id),
    )
    get_db().commit()
    return redirect(url_for("main.users"))


@main_bp.post("/users/<int:user_id>/status")
@admin_only
def update_user_status(user_id):
    if user_id == current_user.id:
        return redirect(url_for("main.users"))

    is_active = 1 if request.form.get("is_active") == "1" else 0
    get_db().execute(
        "UPDATE users SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (is_active, user_id),
    )
    get_db().commit()
    return redirect(url_for("main.users"))


@main_bp.post("/services/vote")
@login_required
def vote_for_service():
    service_id = parse_int(request.form.get("service_id"), minimum=1, default=None)
    client_id = parse_int(request.form.get("client_id"), minimum=1, default=None)
    if service_id is None or client_id is None:
        return redirect(url_for("main.reports"))

    service = get_db().execute(
        "SELECT id FROM services WHERE id = ?",
        (service_id,),
    ).fetchone()
    if service is None:
        return redirect(url_for("main.reports"))

    client = get_db().execute(
        "SELECT id FROM clients WHERE id = ?",
        (client_id,),
    ).fetchone()
    if client is None:
        return redirect(url_for("main.reports"))

    month_key = date.today().strftime("%Y-%m")
    get_db().execute(
        """
        INSERT OR IGNORE INTO service_votes (service_id, client_id, month_key)
        VALUES (?, ?, ?)
        """,
        (service_id, client_id, month_key),
    )
    get_db().commit()
    return redirect(url_for("main.reports"))


@main_bp.get("/clients")
@login_required
def clients():
    if current_user.role == "client":
        return redirect(url_for("main.dashboard"))

    if current_user.role == "business_owner":
        rows = get_db().execute(
            """
            SELECT DISTINCT c.*
            FROM clients AS c
            JOIN appointments AS a ON a.client_id = c.id
            WHERE a.shop_id = ?
            ORDER BY c.full_name ASC
            """,
            (current_business_id(),),
        ).fetchall()
    elif current_user.role == "employee":
        rows = get_db().execute(
            """
            SELECT DISTINCT c.*
            FROM clients AS c
            JOIN appointments AS a ON a.client_id = c.id
            WHERE a.shop_id IS NULL
            ORDER BY c.full_name ASC
            """
        ).fetchall()
    else:
        rows = get_db().execute(
            "SELECT * FROM clients ORDER BY full_name ASC"
        ).fetchall()
    return render_template("clients.html", clients=rows, current_user=current_user)


@main_bp.route("/clients/new", methods=["GET", "POST"])
@admin_only
def create_client():
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        email = (request.form.get("email") or "").strip() or None
        birth_date = request.form.get("birth_date") or None
        profile_photo_url = (request.form.get("profile_photo_url") or "").strip() or None
        notes = (request.form.get("notes") or "").strip() or None

        if not full_name or not phone:
            return render_template(
                "client_form.html",
                client=None,
                error="Nome e telefone são obrigatórios.",
                current_user=current_user,
            )

        get_db().execute(
            """
            INSERT INTO clients (full_name, phone, email, birth_date, profile_photo_url, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (full_name, phone, email, birth_date, profile_photo_url, notes),
        )
        get_db().commit()
        return redirect(url_for("main.clients"))

    return render_template(
        "client_form.html",
        client=None,
        error=None,
        current_user=current_user,
    )


@main_bp.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
@admin_only
def edit_client(client_id):
    client = get_db().execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if client is None:
        return redirect(url_for("main.clients"))

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        email = (request.form.get("email") or "").strip() or None
        birth_date = request.form.get("birth_date") or None
        profile_photo_url = (request.form.get("profile_photo_url") or "").strip() or None
        notes = (request.form.get("notes") or "").strip() or None

        if not full_name or not phone:
            return render_template(
                "client_form.html",
                client=dict(client),
                error="Nome e telefone são obrigatórios.",
                current_user=current_user,
            )

        get_db().execute(
            """
            UPDATE clients
            SET full_name = ?, phone = ?, email = ?, birth_date = ?, profile_photo_url = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (full_name, phone, email, birth_date, profile_photo_url, notes, client_id),
        )
        get_db().commit()
        return redirect(url_for("main.clients"))

    return render_template(
        "client_form.html",
        client=dict(client),
        error=None,
        current_user=current_user,
    )


@main_bp.post("/clients/<int:client_id>/delete")
@admin_only
def delete_client(client_id):
    get_db().execute("DELETE FROM clients WHERE id = ?", (client_id,))
    get_db().commit()
    return redirect(url_for("main.clients"))


@main_bp.get("/barbers")
@login_required
def barbers():
    if current_user.role == "client":
        return redirect(url_for("main.dashboard"))

    query = "SELECT * FROM barbers WHERE 1 = 1"
    scope, params = shop_scope("shop_id")
    rows = get_db().execute(
        query + scope + " ORDER BY full_name ASC",
        params,
    ).fetchall()
    return render_template("barbers.html", barbers=rows, current_user=current_user)


@main_bp.route("/barbers/new", methods=["GET", "POST"])
@admin_required
def create_barber():
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        specialty = (request.form.get("specialty") or "").strip() or None
        status = request.form.get("status") or "active"

        if not full_name or not phone:
            return render_template(
                "barber_form.html",
                barber=None,
                error="Nome e telefone são obrigatórios.",
                current_user=current_user,
            )

        get_db().execute(
            """
            INSERT INTO barbers (shop_id, full_name, phone, specialty, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (current_business_id(), full_name, phone, specialty, status),
        )
        get_db().commit()
        return redirect(url_for("main.barbers"))

    return render_template(
        "barber_form.html",
        barber=None,
        error=None,
        current_user=current_user,
    )


@main_bp.route("/barbers/<int:barber_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_barber(barber_id):
    scope, params = shop_scope("shop_id")
    barber = get_db().execute(
        "SELECT * FROM barbers WHERE id = ?" + scope,
        [barber_id, *params],
    ).fetchone()
    if barber is None:
        return redirect(url_for("main.barbers"))

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        specialty = (request.form.get("specialty") or "").strip() or None
        status = request.form.get("status") or "active"

        if not full_name or not phone:
            return render_template(
                "barber_form.html",
                barber=dict(barber),
                error="Nome e telefone são obrigatórios.",
                current_user=current_user,
            )

        get_db().execute(
            """
            UPDATE barbers
            SET full_name = ?, phone = ?, specialty = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (full_name, phone, specialty, status, barber_id),
        )
        get_db().commit()
        return redirect(url_for("main.barbers"))

    return render_template(
        "barber_form.html",
        barber=dict(barber),
        error=None,
        current_user=current_user,
    )


@main_bp.post("/barbers/<int:barber_id>/delete")
@admin_required
def delete_barber(barber_id):
    scope, params = shop_scope("shop_id")
    get_db().execute(
        "DELETE FROM barbers WHERE id = ?" + scope,
        [barber_id, *params],
    )
    get_db().commit()
    return redirect(url_for("main.barbers"))


@main_bp.get("/services")
@login_required
def services():
    if current_user.role == "client":
        db = get_db()
        shops = db.execute(
            "SELECT id, business_name FROM barbershops ORDER BY business_name COLLATE NOCASE"
        ).fetchall()
        selected_shop_id = parse_int(
            request.args.get("shop_id"), minimum=1, default=None
        )
        if selected_shop_id is None and shops:
            selected_shop_id = shops[0]["id"]
        if selected_shop_id is not None:
            rows = db.execute(
                """
                SELECT * FROM services
                WHERE status = 'active' AND shop_id = ?
                ORDER BY name ASC
                """,
                (selected_shop_id,),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT * FROM services
                WHERE status = 'active' AND shop_id IS NULL
                ORDER BY name ASC
                """
            ).fetchall()
        shop = db.execute(
            "SELECT * FROM barbershops WHERE id = ?",
            (selected_shop_id,),
        ).fetchone() if selected_shop_id is not None else None
    else:
        scope, params = shop_scope("shop_id")
        rows = get_db().execute(
            "SELECT * FROM services WHERE 1 = 1"
            + scope
            + " ORDER BY name ASC",
            params,
        ).fetchall()
        shops = []
        selected_shop_id = current_business_id()
        shop = None
    return render_template(
        "services.html",
        services=rows,
        shops=shops,
        shop=shop,
        selected_shop_id=selected_shop_id,
        current_user=current_user,
    )


@main_bp.route("/services/new", methods=["GET", "POST"])
@admin_required
def create_service():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        image_url_value = request.form.get("image_url") or ""
        price_cents = parse_int(request.form.get("price_cents"), minimum=1, default=0)
        duration_minutes = parse_int(
            request.form.get("duration_minutes"), minimum=1, default=0
        )
        status = request.form.get("status") or "active"

        if (
            not name
            or price_cents <= 0
            or duration_minutes <= 0
        ):
            return render_template(
                "service_form.html",
                service=None,
                error="Nome, preço e duração são obrigatórios.",
                current_user=current_user,
            )

        image_url, image_error = save_service_photo(
            request.files.get("image_file"),
            image_url_value,
        )
        if image_error:
            return render_template(
                "service_form.html",
                service=None,
                error=image_error,
                current_user=current_user,
            )

        get_db().execute(
            """
            INSERT INTO services
                (shop_id, name, description, image_url, price_cents, duration_minutes, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                current_business_id(),
                name,
                description,
                image_url,
                price_cents,
                duration_minutes,
                status,
            ),
        )
        get_db().commit()
        return redirect(url_for("main.services"))

    return render_template(
        "service_form.html",
        service=None,
        error=None,
        current_user=current_user,
    )


@main_bp.route("/services/<int:service_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_service(service_id):
    scope, params = shop_scope("shop_id")
    service = get_db().execute(
        "SELECT * FROM services WHERE id = ?" + scope,
        [service_id, *params],
    ).fetchone()
    if service is None:
        return redirect(url_for("main.services"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        image_url_value = request.form.get("image_url") or ""
        price_cents = parse_int(request.form.get("price_cents"), minimum=1, default=0)
        duration_minutes = parse_int(
            request.form.get("duration_minutes"), minimum=1, default=0
        )
        status = request.form.get("status") or "active"

        if (
            not name
            or price_cents <= 0
            or duration_minutes <= 0
        ):
            return render_template(
                "service_form.html",
                service=dict(service),
                error="Nome, preço e duração são obrigatórios.",
                current_user=current_user,
            )

        image_url, image_error = save_service_photo(
            request.files.get("image_file"),
            image_url_value,
            service["image_url"],
        )
        if image_error:
            return render_template(
                "service_form.html",
                service=dict(service),
                error=image_error,
                current_user=current_user,
            )

        get_db().execute(
            """
            UPDATE services
            SET name = ?, description = ?, image_url = ?, price_cents = ?,
                duration_minutes = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                name,
                description,
                image_url,
                price_cents,
                duration_minutes,
                status,
                service_id,
            ),
        )
        get_db().commit()
        return redirect(url_for("main.services"))

    return render_template(
        "service_form.html",
        service=dict(service),
        error=None,
        current_user=current_user,
    )


@main_bp.post("/services/<int:service_id>/delete")
@admin_required
def delete_service(service_id):
    scope, params = shop_scope("shop_id")
    get_db().execute(
        "DELETE FROM services WHERE id = ?" + scope,
        [service_id, *params],
    )
    get_db().commit()
    return redirect(url_for("main.services"))


@main_bp.get("/appointments")
@login_required
def appointments():
    db = get_db()

    if current_user.role == "client":
        client_profile = get_client_profile_for_current_user()
        if client_profile is None:
            return redirect(url_for("main.dashboard"))

        shops = db.execute(
            "SELECT * FROM barbershops ORDER BY business_name COLLATE NOCASE"
        ).fetchall()
        selected_shop_id = parse_int(
            request.args.get("shop_id"), minimum=1, default=None
        )
        selected_service_id = parse_int(
            request.args.get("service_id"), minimum=1, default=None
        )
        if selected_shop_id is None and selected_service_id is not None:
            selected_service_shop = db.execute(
                "SELECT shop_id FROM services WHERE id = ? AND status = 'active'",
                (selected_service_id,),
            ).fetchone()
            if selected_service_shop and selected_service_shop["shop_id"] is not None:
                selected_shop_id = selected_service_shop["shop_id"]
        if selected_shop_id is None and shops:
            selected_shop_id = shops[0]["id"]

        rows = db.execute(
            """
            SELECT a.*, c.full_name AS client_name, c.profile_photo_url AS client_photo_url,
                   b.full_name AS barber_name, s.name AS service_name, s.price_cents AS service_price_cents
            FROM appointments AS a
            JOIN clients AS c ON c.id = a.client_id
            JOIN barbers AS b ON b.id = a.barber_id
            JOIN services AS s ON s.id = a.service_id
            WHERE a.client_id = ?
            ORDER BY a.starts_at DESC
            """,
            (client_profile["id"],),
        ).fetchall()
        if selected_shop_id is not None:
            barbers = db.execute(
                """
                SELECT id, shop_id, full_name, specialty FROM barbers
                WHERE status = 'active' AND shop_id = ?
                ORDER BY full_name ASC
                """,
                (selected_shop_id,),
            ).fetchall()
            services = db.execute(
                """
                SELECT id, shop_id, name, description, image_url,
                       duration_minutes, price_cents
                FROM services
                WHERE status = 'active' AND shop_id = ?
                ORDER BY name ASC
                """,
                (selected_shop_id,),
            ).fetchall()
        else:
            barbers = db.execute(
                """
                SELECT id, shop_id, full_name, specialty FROM barbers
                WHERE status = 'active' AND shop_id IS NULL
                ORDER BY full_name ASC
                """
            ).fetchall()
            services = db.execute(
                """
                SELECT id, shop_id, name, description, image_url,
                       duration_minutes, price_cents
                FROM services
                WHERE status = 'active' AND shop_id IS NULL
                ORDER BY name ASC
                """
            ).fetchall()
        selected_shop = db.execute(
            "SELECT * FROM barbershops WHERE id = ?",
            (selected_shop_id,),
        ).fetchone() if selected_shop_id is not None else None
        service_shop_clause = (
            "shop_id = ?" if selected_shop_id is not None else "shop_id IS NULL"
        )
        selected_service = db.execute(
            f"""
            SELECT id, name, duration_minutes, price_cents
            FROM services
            WHERE id = ? AND status = 'active' AND {service_shop_clause}
            """,
            (selected_service_id, selected_shop_id)
            if selected_shop_id is not None
            else (selected_service_id,),
        ).fetchone() if selected_service_id is not None else None
        return render_template(
            "appointments.html",
            appointments=rows,
            queue_rows=[],
            clients=[],
            barbers=barbers,
            services=services,
            shops=shops,
            selected_shop=selected_shop,
            selected_shop_id=selected_shop_id,
            selected_service=selected_service,
            selected_service_id=selected_service["id"] if selected_service else None,
            selected_date=request.args.get("appointment_date", ""),
            selected_time=request.args.get("appointment_time", ""),
            today=date.today().isoformat(),
            statuses=APPOINTMENT_STATUSES,
            current_user=current_user,
            client_view=True,
            now_local=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    appointment_scope, appointment_params = shop_scope("shop_id", "a")
    rows = db.execute(
        """
        SELECT a.*, c.full_name AS client_name, c.profile_photo_url AS client_photo_url,
               b.full_name AS barber_name, s.name AS service_name, s.price_cents AS service_price_cents
        FROM appointments AS a
        JOIN clients AS c ON c.id = a.client_id
        JOIN barbers AS b ON b.id = a.barber_id
        JOIN services AS s ON s.id = a.service_id
        WHERE 1 = 1
        """
        + appointment_scope
        + """
        ORDER BY a.starts_at DESC
        """
        ,
        appointment_params,
    ).fetchall()
    if current_user.role in ("business_owner", "employee"):
        appointment_shop_clause = (
            "a.shop_id = ?" if current_user.role == "business_owner" else "a.shop_id IS NULL"
        )
        client_params = (
            (current_business_id(),)
            if current_user.role == "business_owner"
            else ()
        )
        clients = db.execute(
            f"""
            SELECT DISTINCT c.id, c.full_name, c.profile_photo_url
            FROM clients AS c
            JOIN appointments AS a ON a.client_id = c.id
            WHERE {appointment_shop_clause}
            ORDER BY c.full_name ASC
            """,
            client_params,
        ).fetchall()
    else:
        clients = db.execute("SELECT id, full_name, profile_photo_url FROM clients ORDER BY full_name ASC").fetchall()
    barber_scope, barber_params = shop_scope("shop_id")
    barbers = db.execute(
        "SELECT id, full_name FROM barbers WHERE 1 = 1"
        + barber_scope
        + " ORDER BY full_name ASC",
        barber_params,
    ).fetchall()
    services = db.execute(
        "SELECT id, name, duration_minutes, price_cents FROM services WHERE 1 = 1"
        + shop_scope("shop_id")[0]
        + " ORDER BY name ASC",
        shop_scope("shop_id")[1],
    ).fetchall()

    queue_rows = []
    if current_user.role == "employee":
        queue_rows = db.execute(
            """
            SELECT a.*, c.full_name AS client_name, c.profile_photo_url AS client_photo_url,
                   b.full_name AS barber_name, s.name AS service_name, s.price_cents AS service_price_cents
            FROM appointments AS a
            JOIN clients AS c ON c.id = a.client_id
            JOIN barbers AS b ON b.id = a.barber_id
            JOIN services AS s ON s.id = a.service_id
            WHERE date(a.starts_at) = date('now', 'localtime')
            """
            + appointment_scope
            + """
            ORDER BY a.starts_at ASC
            """
            ,
            appointment_params,
        ).fetchall()

    return render_template(
        "appointments.html",
        appointments=rows,
        queue_rows=queue_rows,
        clients=clients,
        barbers=barbers,
        services=services,
        shops=[],
        selected_shop=None,
        selected_shop_id=current_business_id(),
        statuses=APPOINTMENT_STATUSES,
        current_user=current_user,
        client_view=False,
    )


@main_bp.get("/appointments/availability")
@login_required
def appointment_availability():
    if current_user.role != "client":
        return jsonify(error="A consulta de horários está disponível para clientes."), 403

    shop_id_value = request.args.get("shop_id")
    shop_id = parse_int(shop_id_value, minimum=1, default=None)
    barber_id = parse_int(request.args.get("barber_id"), minimum=1, default=None)
    service_id = parse_int(request.args.get("service_id"), minimum=1, default=None)
    try:
        selected_date = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        return jsonify(error="Informe uma data válida."), 400

    if (
        (shop_id_value and shop_id is None)
        or barber_id is None
        or service_id is None
    ):
        return jsonify(error="Selecione a barbearia, o barbeiro e o serviço."), 400

    db = get_db()
    if shop_id is not None and db.execute(
        "SELECT 1 FROM barbershops WHERE id = ?",
        (shop_id,),
    ).fetchone() is None:
        return jsonify(error="A barbearia selecionada não foi encontrada."), 404

    shop_filter = "shop_id = ?" if shop_id is not None else "shop_id IS NULL"
    scope_parameters = (shop_id,) if shop_id is not None else ()
    barber = db.execute(
        f"""
        SELECT id, shop_id FROM barbers
        WHERE id = ? AND status = 'active' AND {shop_filter}
        """,
        (barber_id, *scope_parameters),
    ).fetchone()
    service = db.execute(
        f"""
        SELECT id, shop_id, duration_minutes FROM services
        WHERE id = ? AND status = 'active' AND {shop_filter}
        """,
        (service_id, *scope_parameters),
    ).fetchone()
    if barber is None or service is None or barber["shop_id"] != service["shop_id"]:
        return jsonify(error="O barbeiro e o serviço não estão disponíveis nesta barbearia."), 400

    day_start = datetime.combine(selected_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    busy_rows = db.execute(
        """
        SELECT starts_at, ends_at FROM appointments
        WHERE barber_id = ?
          AND status != 'cancelled'
          AND starts_at < ?
          AND ends_at > ?
        """,
        (
            barber_id,
            day_end.strftime("%Y-%m-%d %H:%M:%S"),
            day_start.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    ).fetchall()
    busy_intervals = [
        (
            datetime.fromisoformat(row["starts_at"]),
            datetime.fromisoformat(row["ends_at"]),
        )
        for row in busy_rows
    ]

    duration = timedelta(minutes=int(service["duration_minutes"]))
    now = datetime.now()
    slots = []
    for minute_of_day in range(
        BOOKING_OPEN_MINUTE,
        BOOKING_CLOSE_MINUTE,
        BOOKING_SLOT_MINUTES,
    ):
        starts_at = day_start + timedelta(minutes=minute_of_day)
        ends_at = starts_at + duration
        outside_business_hours = ends_at > day_end
        already_passed = starts_at <= now
        occupied = any(
            starts_at < busy_end and ends_at > busy_start
            for busy_start, busy_end in busy_intervals
        )
        slots.append(
            {
                "time": starts_at.strftime("%H:%M"),
                "available": (
                    not outside_business_hours and not already_passed and not occupied
                ),
                "reason": (
                    "Fora do expediente"
                    if outside_business_hours
                    else "Ocupado"
                    if occupied
                    else "Horário já passou"
                    if already_passed
                    else None
                ),
            }
        )

    return jsonify(
        date=selected_date.isoformat(),
        duration_minutes=int(service["duration_minutes"]),
        slots=slots,
    )


@main_bp.route("/appointments/new", methods=["POST"])
@login_required
def create_appointment():
    is_client = current_user.role == "client"
    shop_id = (
        parse_int(request.form.get("shop_id"), minimum=1, default=None)
        if is_client
        else current_business_id()
    )
    client_profile = get_client_profile_for_current_user() if is_client else None
    client_id = (
        client_profile["id"]
        if client_profile is not None
        else parse_int(request.form.get("client_id"), minimum=1, default=None)
    )
    barber_id = parse_int(request.form.get("barber_id"), minimum=1, default=None)
    service_id = parse_int(request.form.get("service_id"), minimum=1, default=None)
    starts_at = request.form.get("starts_at")
    appointment_date = request.form.get("appointment_date", "")
    appointment_time = request.form.get("appointment_time", "")
    if is_client and appointment_date and appointment_time:
        starts_at = f"{appointment_date}T{appointment_time}"
    status = request.form.get("status") or "scheduled"

    def redirect_to_booking():
        if not is_client:
            return redirect(url_for("main.appointments"))
        return redirect(
            url_for(
                "main.appointments",
                shop_id=shop_id,
                service_id=service_id,
                appointment_date=appointment_date,
                appointment_time=appointment_time,
            )
        )

    if client_id is None or barber_id is None or service_id is None or not starts_at:
        flash("Selecione barbeiro, serviço e horário para continuar.", "danger")
        return redirect_to_booking()

    client = get_db().execute(
        "SELECT id FROM clients WHERE id = ?",
        (client_id,),
    ).fetchone()
    if client is None:
        flash("O cadastro do cliente não foi encontrado.", "danger")
        return redirect_to_booking()
    if current_user.role == "business_owner":
        client_belongs_to_shop = get_db().execute(
            """
            SELECT 1 FROM appointments
            WHERE client_id = ? AND shop_id = ?
            LIMIT 1
            """,
            (client_id, shop_id),
        ).fetchone()
        if client_belongs_to_shop is None:
            flash("Esse cliente ainda não possui vínculo com esta barbearia.", "danger")
            return redirect_to_booking()

    active_filter = " AND status = 'active'" if is_client else ""
    service = get_db().execute(
        """
        SELECT id, shop_id, duration_minutes, price_cents
        FROM services WHERE id = ?
        """
        + active_filter
        + (" AND shop_id = ?" if shop_id is not None else " AND shop_id IS NULL"),
        (service_id, shop_id) if shop_id is not None else (service_id,),
    ).fetchone()
    if service is None:
        flash("O serviço selecionado não está disponível.", "danger")
        return redirect_to_booking()

    barber = get_db().execute(
        """
        SELECT id, shop_id FROM barbers WHERE id = ?
        """
        + active_filter
        + (" AND shop_id = ?" if shop_id is not None else " AND shop_id IS NULL"),
        (barber_id, shop_id) if shop_id is not None else (barber_id,),
    ).fetchone()
    if barber is None:
        flash("O barbeiro selecionado não está disponível.", "danger")
        return redirect_to_booking()
    if service["shop_id"] != barber["shop_id"]:
        flash("O barbeiro e o serviço precisam pertencer à mesma barbearia.", "danger")
        return redirect_to_booking()

    try:
        starts_dt = datetime.fromisoformat(starts_at.replace("Z", ""))
    except ValueError:
        flash("Informe uma data e horário válidos.", "danger")
        return redirect_to_booking()

    if starts_dt.tzinfo is not None:
        flash("Informe a data e o horário sem fuso horário.", "danger")
        return redirect_to_booking()

    if is_client and starts_dt <= datetime.now():
        flash("Escolha um horário futuro para o agendamento.", "danger")
        return redirect_to_booking()

    ends_dt = starts_dt + timedelta(minutes=int(service["duration_minutes"]))
    if is_client and (
        starts_dt.hour * 60 + starts_dt.minute < BOOKING_OPEN_MINUTE
        or starts_dt.minute % BOOKING_SLOT_MINUTES != 0
        or starts_dt.second != 0
        or ends_dt > datetime.combine(
            starts_dt.date() + timedelta(days=1),
            datetime.min.time(),
        )
    ):
        flash(
            "Escolha um horário do expediente (05:00–00:00) em intervalos de 15 minutos.",
            "danger",
        )
        return redirect_to_booking()

    starts_value = starts_dt.strftime("%Y-%m-%d %H:%M:%S")
    ends_value = ends_dt.strftime("%Y-%m-%d %H:%M:%S")
    conflict = get_db().execute(
        """
        SELECT 1
        FROM appointments
        WHERE barber_id = ?
          AND status != 'cancelled'
          AND starts_at < ?
          AND ends_at > ?
        LIMIT 1
        """,
        (barber_id, ends_value, starts_value),
    ).fetchone()
    if conflict:
        flash("Esse horário acabou de ser ocupado. Escolha outro horário.", "danger")
        return redirect_to_booking()

    try:
        appointment_cursor = get_db().execute(
            """
            INSERT INTO appointments
                (shop_id, client_id, barber_id, service_id, starts_at, ends_at, price_cents, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                shop_id,
                client_id,
                barber_id,
                service_id,
                starts_value,
                ends_value,
                int(service["price_cents"]),
                "scheduled" if is_client else (
                    status if status in APPOINTMENT_STATUSES else "scheduled"
                ),
            ),
        )
        get_db().commit()
    except sqlite3.IntegrityError as error:
        if "appointment_conflict" not in str(error):
            raise
        flash("Esse horário acabou de ser ocupado. Escolha outro horário.", "danger")
        return redirect_to_booking()

    if is_client:
        return redirect(
            url_for(
                "main.appointment_confirmation",
                appointment_id=appointment_cursor.lastrowid,
            )
        )
    flash("Agendamento realizado com sucesso.", "success")
    return redirect(url_for("main.appointments"))


@main_bp.get("/appointments/<int:appointment_id>/confirmation")
@login_required
def appointment_confirmation(appointment_id):
    client_profile = get_client_profile_for_current_user()
    if client_profile is None:
        return redirect(url_for("main.dashboard"))

    appointment = get_db().execute(
        """
        SELECT a.id, a.shop_id, a.starts_at, a.ends_at, a.price_cents, a.status,
               b.full_name AS barber_name,
               s.name AS service_name, s.duration_minutes,
               sh.business_name AS shop_name, sh.address AS shop_address,
               sh.latitude AS shop_latitude, sh.longitude AS shop_longitude,
               sh.location_is_approximate
        FROM appointments AS a
        JOIN barbers AS b ON b.id = a.barber_id
        JOIN services AS s ON s.id = a.service_id
        LEFT JOIN barbershops AS sh ON sh.id = a.shop_id
        WHERE a.id = ? AND a.client_id = ?
        """,
        (appointment_id, client_profile["id"]),
    ).fetchone()
    if appointment is None:
        return redirect(url_for("main.appointments"))

    starts_at = datetime.fromisoformat(appointment["starts_at"])
    ends_at = datetime.fromisoformat(appointment["ends_at"])
    return render_template(
        "appointment_confirmation.html",
        appointment=appointment,
        appointment_date=starts_at.strftime("%d/%m/%Y"),
        start_time=starts_at.strftime("%H:%M"),
        end_time=ends_at.strftime("%H:%M"),
        current_user=current_user,
    )


@main_bp.route("/appointments/<int:appointment_id>/status", methods=["POST"])
@login_required
def update_appointment_status(appointment_id):
    if current_user.role == "client":
        return redirect(url_for("main.appointments"))

    status = request.form.get("status") or "scheduled"
    if status not in APPOINTMENT_STATUSES:
        status = "scheduled"

    scope, params = shop_scope("shop_id")
    get_db().execute(
        "UPDATE appointments SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        + scope,
        [status, appointment_id, *params],
    )
    get_db().commit()
    return redirect(url_for("main.appointments"))


@main_bp.route("/appointments/<int:appointment_id>/edit", methods=["GET", "POST"])
@login_required
def edit_appointment(appointment_id):
    if current_user.role == "client":
        return redirect(url_for("main.appointments"))

    scope, params = shop_scope("a.shop_id")
    appointment = get_db().execute(
        """
        SELECT a.*, c.full_name AS client_name, b.full_name AS barber_name, s.name AS service_name
        FROM appointments AS a
        JOIN clients AS c ON c.id = a.client_id
        JOIN barbers AS b ON b.id = a.barber_id
        JOIN services AS s ON s.id = a.service_id
        WHERE a.id = ?
        """
        + scope
        ,
        [appointment_id, *params],
    ).fetchone()
    if appointment is None:
        return redirect(url_for("main.appointments"))

    service_scope, service_params = shop_scope("shop_id")
    barber_scope, barber_params = shop_scope("shop_id")
    if request.method == "POST":
        client_id = parse_int(request.form.get("client_id"), minimum=1, default=None)
        barber_id = parse_int(request.form.get("barber_id"), minimum=1, default=None)
        service_id = parse_int(request.form.get("service_id"), minimum=1, default=None)
        starts_at = request.form.get("starts_at")
        status = request.form.get("status") or appointment["status"]

        if client_id is None or barber_id is None or service_id is None or not starts_at:
            return redirect(url_for("main.appointments"))

        service = get_db().execute(
            "SELECT id, shop_id, duration_minutes, price_cents FROM services WHERE id = ?"
            + service_scope,
            [service_id, *service_params],
        ).fetchone()
        if service is None:
            return redirect(url_for("main.appointments"))
        barber = get_db().execute(
            "SELECT id, shop_id FROM barbers WHERE id = ?" + barber_scope,
            [barber_id, *barber_params],
        ).fetchone()
        if barber is None or service["shop_id"] != barber["shop_id"]:
            return redirect(url_for("main.appointments"))
        if current_user.role == "business_owner":
            client_in_shop = get_db().execute(
                """
                SELECT 1 FROM appointments
                WHERE client_id = ? AND shop_id = ?
                LIMIT 1
                """,
                (client_id, current_business_id()),
            ).fetchone()
            if client_in_shop is None:
                return redirect(url_for("main.appointments"))

        try:
            starts_dt = datetime.fromisoformat(starts_at.replace("Z", ""))
        except ValueError:
            return redirect(url_for("main.appointments"))

        ends_dt = starts_dt + timedelta(minutes=int(service["duration_minutes"]))
        try:
            get_db().execute(
                """
                UPDATE appointments
                SET client_id = ?, barber_id = ?, service_id = ?, starts_at = ?,
                    ends_at = ?, price_cents = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    client_id,
                    barber_id,
                    service_id,
                    starts_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    ends_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    int(service["price_cents"]),
                    status if status in APPOINTMENT_STATUSES else appointment["status"],
                    appointment_id,
                ),
            )
            get_db().commit()
        except sqlite3.IntegrityError:
            pass

        return redirect(url_for("main.appointments"))

    if current_user.role in ("business_owner", "employee"):
        client_scope = (
            " AND EXISTS (SELECT 1 FROM appointments a WHERE a.client_id = clients.id AND a.shop_id = ?)"
            if current_user.role == "business_owner"
            else " AND EXISTS (SELECT 1 FROM appointments a WHERE a.client_id = clients.id AND a.shop_id IS NULL)"
        )
        client_params = (current_business_id(),) if current_user.role == "business_owner" else ()
        clients = get_db().execute(
            "SELECT id, full_name FROM clients WHERE 1 = 1"
            + client_scope
            + " ORDER BY full_name ASC",
            client_params,
        ).fetchall()
    else:
        clients = get_db().execute("SELECT id, full_name FROM clients ORDER BY full_name ASC").fetchall()
    barber_scope, barber_params = shop_scope("shop_id")
    barbers = get_db().execute(
        "SELECT id, full_name FROM barbers WHERE 1 = 1"
        + barber_scope
        + " ORDER BY full_name ASC",
        barber_params,
    ).fetchall()
    services = get_db().execute(
        "SELECT id, name, duration_minutes, price_cents FROM services WHERE 1 = 1"
        + service_scope
        + " ORDER BY name ASC",
        service_params,
    ).fetchall()

    return render_template(
        "appointment_form.html",
        appointment=dict(appointment),
        clients=clients,
        barbers=barbers,
        services=services,
        statuses=APPOINTMENT_STATUSES,
        current_user=current_user,
    )


@main_bp.post("/appointments/<int:appointment_id>/cancel")
@login_required
def cancel_appointment(appointment_id):
    if current_user.role == "client":
        client_profile = get_client_profile_for_current_user()
        if client_profile is not None:
            get_db().execute(
                """
                UPDATE appointments
                SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND client_id = ?
                  AND status IN ('scheduled', 'confirmed')
                  AND starts_at > datetime('now', 'localtime')
                """,
                (appointment_id, client_profile["id"]),
            )
            get_db().commit()
        return redirect(url_for("main.appointments"))

    scope, params = shop_scope("shop_id")
    get_db().execute(
        "UPDATE appointments SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        + scope,
        [appointment_id, *params],
    )
    get_db().commit()
    return redirect(url_for("main.appointments"))


@main_bp.post("/appointments/<int:appointment_id>/delete")
@login_required
def delete_appointment(appointment_id):
    if current_user.role == "client":
        return redirect(url_for("main.appointments"))

    scope, params = shop_scope("shop_id")
    get_db().execute(
        "DELETE FROM appointments WHERE id = ?" + scope,
        [appointment_id, *params],
    )
    get_db().commit()
    return redirect(url_for("main.appointments"))
