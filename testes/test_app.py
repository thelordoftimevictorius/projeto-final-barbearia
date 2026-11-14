import base64
import io
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import create_app


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "test.sqlite3"
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE": str(database_path),
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_home_page_loads(self):
        response = self.client.get("/")
        page = response.get_data()
        response.close()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Barber Manager", page)
        self.assertIn(b"pagina_inicial.css", page)
        self.assertIn(b'href="/cadastro/cliente"', page)
        self.assertIn(b'id="barbearias"', page)

    def test_homepage_stylesheet_is_served(self):
        response = self.client.get("/static/css/pagina_inicial.css")
        stylesheet = response.get_data()
        response.close()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b".landing-hero", stylesheet)

    def test_admin_login_flow(self):
        with self.app.app_context():
            from banco_de_dados.db import create_default_admin, init_db

            init_db()
            create_default_admin()

        response = self.client.post(
            "/login",
            data={"email": "admin@barber.local", "password": "admin123"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")

    def test_client_can_register_and_book_a_compatible_appointment(self):
        registration = self.client.post(
            "/cadastro/cliente",
            data={
                "full_name": "Cliente Online",
                "email": "cliente.online@example.com",
                "phone": "11987654321",
                "password": "senha-segura-123",
                "password_confirmation": "senha-segura-123",
            },
            follow_redirects=False,
        )
        self.assertEqual(registration.status_code, 302)
        self.assertEqual(registration.headers["Location"], "/appointments")

        with self.app.app_context():
            from banco_de_dados.db import get_db

            database = get_db()
            barber_id = database.execute(
                "INSERT INTO barbers (full_name, phone) VALUES (?, ?)",
                ("Barbeiro online", "11911112222"),
            ).lastrowid
            service_id = database.execute(
                """
                INSERT INTO services (name, price_cents, duration_minutes)
                VALUES (?, ?, ?)
                """,
                ("Corte online", 5000, 30),
            ).lastrowid
            database.commit()

        appointments_page = self.client.get("/appointments")
        self.assertEqual(appointments_page.status_code, 200)
        self.assertIn(b"Barbeiro online", appointments_page.data)
        self.assertIn(b"Corte online", appointments_page.data)

        appointment_start = (
            date.today() + timedelta(days=1)
        ).strftime("%Y-%m-%d") + "T10:00"
        booking = self.client.post(
            "/appointments/new",
            data={
                "barber_id": str(barber_id),
                "service_id": str(service_id),
                "starts_at": appointment_start,
            },
            follow_redirects=False,
        )
        self.assertEqual(booking.status_code, 302)
        self.assertRegex(
            booking.headers["Location"],
            r"/appointments/\d+/confirmation$",
        )
        confirmation = self.client.get(booking.headers["Location"])
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn(b"Agendamento confirmado!", confirmation.data)
        self.assertIn(b"Corte online", confirmation.data)

        with self.app.app_context():
            from banco_de_dados.db import get_db

            appointment = get_db().execute(
                """
                SELECT a.id, a.price_cents, a.status, c.email
                FROM appointments AS a
                JOIN clients AS c ON c.id = a.client_id
                """
            ).fetchone()
            self.assertEqual(appointment["email"], "cliente.online@example.com")
            self.assertEqual(appointment["price_cents"], 5000)
            self.assertEqual(appointment["status"], "scheduled")

        conflicting_booking = self.client.post(
            "/appointments/new",
            data={
                "barber_id": str(barber_id),
                "service_id": str(service_id),
                "starts_at": appointment_start,
            },
            follow_redirects=True,
        )
        self.assertIn(b"Esse hor\xc3\xa1rio acabou de ser ocupado", conflicting_booking.data)
        with self.app.app_context():
            from banco_de_dados.db import get_db

            self.assertEqual(
                get_db().execute("SELECT COUNT(*) FROM appointments").fetchone()[0],
                1,
            )

        appointment_id = appointment["id"]
        cancelled = self.client.post(
            f"/appointments/{appointment_id}/cancel",
            follow_redirects=False,
        )
        self.assertEqual(cancelled.status_code, 302)
        with self.app.app_context():
            from banco_de_dados.db import get_db

            status = get_db().execute(
                "SELECT status FROM appointments WHERE id = ?",
                (appointment_id,),
            ).fetchone()["status"]
        self.assertEqual(status, "cancelled")

    def test_client_registration_rejects_mismatched_passwords(self):
        response = self.client.post(
            "/cadastro/cliente",
            data={
                "full_name": "Cliente Inválido",
                "email": "invalido@example.com",
                "phone": "11987654321",
                "password": "senha-segura-123",
                "password_confirmation": "outra-senha",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"As senhas n\xc3\xa3o conferem", response.data)

    def test_existing_client_profile_is_linked_to_its_account(self):
        with self.app.app_context():
            from banco_de_dados.db import get_db, init_db

            database = get_db()
            user_id = database.execute(
                """
                INSERT INTO users (full_name, email, password_hash, role)
                VALUES (?, ?, ?, 'client')
                """,
                ("Cliente legado", "legado@example.com", "hash"),
            ).lastrowid
            database.execute(
                """
                INSERT INTO clients (full_name, phone, email)
                VALUES (?, ?, ?)
                """,
                ("Cliente legado", "11912345678", "legado@example.com"),
            )
            database.commit()

            init_db()
            profile = database.execute(
                "SELECT user_id FROM clients WHERE email = ?",
                ("legado@example.com",),
            ).fetchone()

        self.assertEqual(profile["user_id"], user_id)

    def test_database_schema_initializes(self):
        with self.app.app_context():
            from banco_de_dados.db import get_db, init_db

            init_db()
            table_names = {
                row["name"]
                for row in get_db().execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

        self.assertTrue(
            {"users", "clients", "barbers", "services", "appointments"}
            .issubset(table_names)
        )

    def test_appointment_status_update(self):
        with self.app.app_context():
            from banco_de_dados.db import create_default_admin, get_db, init_db

            init_db()
            create_default_admin()
            database = get_db()
            client_id = database.execute(
                "INSERT INTO clients (full_name, phone) VALUES (?, ?)",
                ("Cliente do agendamento", "11987878787"),
            ).lastrowid
            barber_id = database.execute(
                "INSERT INTO barbers (full_name, phone) VALUES (?, ?)",
                ("Barbeiro do agendamento", "11979797979"),
            ).lastrowid
            service_id = database.execute(
                "INSERT INTO services (name, price_cents, duration_minutes) VALUES (?, ?, ?)",
                ("Corte do agendamento", 4000, 30),
            ).lastrowid
            database.execute(
                """
                INSERT INTO appointments
                    (client_id, barber_id, service_id, starts_at, ends_at, price_cents)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    client_id,
                    barber_id,
                    service_id,
                    "2026-11-12 10:00:00",
                    "2026-11-12 10:30:00",
                    4000,
                ),
            )
            database.commit()

        self.client.post(
            "/login",
            data={"email": "admin@barber.local", "password": "admin123"},
            follow_redirects=False,
        )
        response = self.client.post(
            "/appointments/1/status",
            data={"status": "confirmed"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            status = get_db().execute(
                "SELECT status FROM appointments WHERE id = 1"
            ).fetchone()["status"]
        self.assertEqual(status, "confirmed")

    def test_database_prevents_overlapping_appointments(self):
        with self.app.app_context():
            from banco_de_dados.db import get_db, init_db

            init_db()
            database = get_db()
            client_id = database.execute(
                "INSERT INTO clients (full_name, phone) VALUES (?, ?)",
                ("Cliente de teste", "11999999999"),
            ).lastrowid
            barber_id = database.execute(
                "INSERT INTO barbers (full_name, phone) VALUES (?, ?)",
                ("Barbeiro de teste", "11888888888"),
            ).lastrowid
            service_id = database.execute(
                """
                INSERT INTO services (name, price_cents, duration_minutes)
                VALUES (?, ?, ?)
                """,
                ("Corte de teste", 5000, 30),
            ).lastrowid
            appointment = (
                client_id,
                barber_id,
                service_id,
                "2026-11-10 10:00:00",
                "2026-11-10 10:30:00",
                5000,
            )
            database.execute(
                """
                INSERT INTO appointments
                    (client_id, barber_id, service_id, starts_at, ends_at, price_cents)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                appointment,
            )

            with self.assertRaisesRegex(sqlite3.IntegrityError, "appointment_conflict"):
                database.execute(
                    """
                    INSERT INTO appointments
                        (client_id, barber_id, service_id, starts_at, ends_at, price_cents)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        client_id,
                        barber_id,
                        service_id,
                        "2026-11-10 10:15:00",
                        "2026-11-10 10:45:00",
                        5000,
                    ),
                )

    def test_invalid_numeric_inputs_are_handled_gracefully(self):
        with self.app.app_context():
            from banco_de_dados.db import create_default_admin, init_db

            init_db()
            create_default_admin()

        self.client.post(
            "/login",
            data={"email": "admin@barber.local", "password": "admin123"},
            follow_redirects=False,
        )

        invalid_service_response = self.client.post(
            "/services/new",
            data={
                "name": "Corte inválido",
                "description": "Teste",
                "price_cents": "abc",
                "duration_minutes": "45",
                "status": "active",
            },
            follow_redirects=False,
        )
        self.assertEqual(invalid_service_response.status_code, 200)
        self.assertIn(b"Nome, pre", invalid_service_response.data)

        invalid_appointment_response = self.client.post(
            "/appointments/new",
            data={
                "client_id": "abc",
                "barber_id": "1",
                "service_id": "1",
                "starts_at": "2026-11-10T10:00:00",
                "status": "scheduled",
            },
            follow_redirects=False,
        )
        self.assertEqual(invalid_appointment_response.status_code, 302)
        self.assertEqual(invalid_appointment_response.headers["Location"], "/appointments")

    def test_dashboard_lists_today_appointments(self):
        with self.app.app_context():
            from banco_de_dados.db import create_default_admin, get_db, init_db

            init_db()
            create_default_admin()
            database = get_db()
            client_id = database.execute(
                "INSERT INTO clients (full_name, phone) VALUES (?, ?)",
                ("Cliente do dia", "11900000000"),
            ).lastrowid
            barber_id = database.execute(
                "INSERT INTO barbers (full_name, phone) VALUES (?, ?)",
                ("Barbeiro do dia", "11800000000"),
            ).lastrowid
            service_id = database.execute(
                "INSERT INTO services (name, price_cents, duration_minutes) VALUES (?, ?, ?)",
                ("Corte do dia", 4500, 30),
            ).lastrowid
            database.execute(
                """
                INSERT INTO appointments (client_id, barber_id, service_id, starts_at, ends_at, price_cents, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_id,
                    barber_id,
                    service_id,
                    f"{date.today().isoformat()} 09:00:00",
                    f"{date.today().isoformat()} 09:30:00",
                    4500,
                    "scheduled",
                ),
            )
            database.commit()

        self.client.post(
            "/login",
            data={"email": "admin@barber.local", "password": "admin123"},
            follow_redirects=False,
        )
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Agenda de hoje", response.data)
        self.assertIn(b"Cliente do dia", response.data)

    def test_employee_can_login_and_reports_page_is_accessible(self):
        with self.app.app_context():
            from banco_de_dados.db import create_default_users, init_db

            init_db()
            create_default_users()

        login_response = self.client.post(
            "/login",
            data={"email": "funcionario@barber.local", "password": "funcionario123"},
            follow_redirects=False,
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.headers["Location"], "/dashboard")

        response = self.client.get("/reports")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Relat", response.data)

        clients_response = self.client.get("/clients")
        self.assertEqual(clients_response.status_code, 200)

        services_response = self.client.get("/services")
        self.assertEqual(services_response.status_code, 200)

    def test_employee_can_view_queue_and_mark_no_show(self):
        with self.app.app_context():
            from banco_de_dados.db import create_default_users, get_db, init_db

            init_db()
            create_default_users()
            database = get_db()
            client_id = database.execute(
                "INSERT INTO clients (full_name, phone, profile_photo_url) VALUES (?, ?, ?)",
                ("Cliente da fila", "11987654321", "https://example.com/avatar.jpg"),
            ).lastrowid
            barber_id = database.execute(
                "INSERT INTO barbers (full_name, phone) VALUES (?, ?)",
                ("Barbeiro da fila", "11912345678"),
            ).lastrowid
            service_id = database.execute(
                "INSERT INTO services (name, price_cents, duration_minutes) VALUES (?, ?, ?)",
                ("Corte da fila", 4500, 30),
            ).lastrowid
            database.execute(
                """
                INSERT INTO appointments (client_id, barber_id, service_id, starts_at, ends_at, price_cents, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_id,
                    barber_id,
                    service_id,
                    "2026-11-10 09:30:00",
                    "2026-11-10 10:00:00",
                    4500,
                    "scheduled",
                ),
            )
            database.commit()

        self.client.post(
            "/login",
            data={"email": "funcionario@barber.local", "password": "funcionario123"},
            follow_redirects=False,
        )

        queue_response = self.client.get("/appointments")
        self.assertEqual(queue_response.status_code, 200)
        self.assertIn(b"Fila de atendimento", queue_response.data)
        self.assertIn(b"Cliente da fila", queue_response.data)

        no_show_response = self.client.post(
            "/appointments/1/status",
            data={"status": "no_show"},
            follow_redirects=False,
        )
        self.assertEqual(no_show_response.status_code, 302)

        with self.app.app_context():
            status = get_db().execute(
                "SELECT status FROM appointments WHERE id = 1"
            ).fetchone()["status"]
            self.assertEqual(status, "no_show")

    def test_admin_can_manage_client_barber_and_service_registers(self):
        with self.app.app_context():
            from banco_de_dados.db import create_default_admin, get_db, init_db

            init_db()
            create_default_admin()

        self.client.post(
            "/login",
            data={"email": "admin@barber.local", "password": "admin123"},
            follow_redirects=False,
        )

        client_response = self.client.post(
            "/clients/new",
            data={"full_name": "Cliente do admin", "phone": "11999999999"},
            follow_redirects=False,
        )
        barber_response = self.client.post(
            "/barbers/new",
            data={
                "full_name": "Barbeiro do admin",
                "phone": "11888888888",
                "specialty": "Corte clássico",
                "status": "active",
            },
            follow_redirects=False,
        )
        service_response = self.client.post(
            "/services/new",
            data={
                "name": "Corte do admin",
                "description": "Serviço cadastrado pelo administrador",
                "price_cents": "3500",
                "duration_minutes": "30",
                "status": "active",
            },
            follow_redirects=False,
        )

        self.assertEqual(client_response.status_code, 302)
        self.assertEqual(barber_response.status_code, 302)
        self.assertEqual(service_response.status_code, 302)

        with self.app.app_context():
            database = get_db()
            self.assertEqual(
                database.execute(
                    "SELECT COUNT(*) FROM clients WHERE full_name = ?",
                    ("Cliente do admin",),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                database.execute(
                    "SELECT COUNT(*) FROM barbers WHERE full_name = ?",
                    ("Barbeiro do admin",),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                database.execute(
                    "SELECT COUNT(*) FROM services WHERE name = ?",
                    ("Corte do admin",),
                ).fetchone()[0],
                1,
            )

    def test_admin_can_manage_user_roles_and_status(self):
        with self.app.app_context():
            from banco_de_dados.db import create_default_users, get_db, init_db

            init_db()
            create_default_users()

        self.client.post(
            "/login",
            data={"email": "admin@barber.local", "password": "admin123"},
            follow_redirects=False,
        )

        users_response = self.client.get("/users")
        self.assertEqual(users_response.status_code, 200)
        self.assertIn(b"Usu\xc3\xa1rios", users_response.data)

        role_response = self.client.post(
            "/users/2/role",
            data={"role": "admin"},
            follow_redirects=False,
        )
        self.assertEqual(role_response.status_code, 302)
        self.assertEqual(role_response.headers["Location"], "/users")

        with self.app.app_context():
            role = get_db().execute(
                "SELECT role FROM users WHERE id = 2"
            ).fetchone()["role"]
            self.assertEqual(role, "admin")

    def test_voting_ranks_top_services_for_current_month(self):
        with self.app.app_context():
            from banco_de_dados.db import create_default_users, get_db, init_db

            init_db()
            create_default_users()
            database = get_db()
            client_id = database.execute(
                "INSERT INTO clients (full_name, phone) VALUES (?, ?)",
                ("Cliente votante", "11911111111"),
            ).lastrowid
            service_id = database.execute(
                "INSERT INTO services (name, price_cents, duration_minutes) VALUES (?, ?, ?)",
                ("Voto premium", 6000, 45),
            ).lastrowid
            database.commit()

        self.client.post(
            "/login",
            data={"email": "admin@barber.local", "password": "admin123"},
            follow_redirects=False,
        )

        vote_response = self.client.post(
            "/services/vote",
            data={"service_id": str(service_id), "client_id": str(client_id)},
            follow_redirects=False,
        )
        self.assertEqual(vote_response.status_code, 302)
        self.assertEqual(vote_response.headers["Location"], "/reports")

        dashboard_response = self.client.get("/dashboard")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertIn(b"Top 10 do m\xc3\xaas", dashboard_response.data)
        self.assertIn(b"Faturamento desta semana", dashboard_response.data)

        with self.app.app_context():
            vote_count = get_db().execute(
                "SELECT COUNT(*) AS total FROM service_votes WHERE service_id = ?",
                (service_id,),
            ).fetchone()["total"]
            self.assertEqual(vote_count, 1)

    def test_client_login_and_dashboard_are_available(self):
        with self.app.app_context():
            from banco_de_dados.db import create_default_users, init_db

            init_db()
            create_default_users()

        login_response = self.client.post(
            "/login",
            data={"email": "cliente@barber.local", "password": "cliente123"},
            follow_redirects=False,
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.headers["Location"], "/dashboard")

        dashboard_response = self.client.get("/dashboard")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertIn(b"Meus agendamentos", dashboard_response.data)

        appointments_response = self.client.get("/appointments")
        self.assertEqual(appointments_response.status_code, 200)
        self.assertIn(b"Meus agendamentos", appointments_response.data)

    def test_barbershop_signup_catalog_map_and_booking_are_shop_scoped(self):
        registration = self.client.post(
            "/cadastro/barbearia",
            data={
                "full_name": "Responsável Badboys",
                "business_name": "Barbearia Badboys",
                "email": "demobarber@teste.com",
                "phone": "87999786141",
                "address": "Praça Doutor Sérgio Magalhães, Centro, Serra Talhada - PE",
                "latitude": "-7.9913239",
                "longitude": "-38.3002140",
                "location_is_approximate": "on",
                "password": "senha-temporaria-123",
                "password_confirmation": "senha-temporaria-123",
            },
            follow_redirects=False,
        )
        self.assertEqual(registration.status_code, 302)
        self.assertEqual(registration.headers["Location"], "/dashboard")
        self.assertIn(b"Barbearia Badboys", self.client.get("/dashboard").data)

        with self.app.app_context():
            from banco_de_dados.db import get_db

            database = get_db()
            shop = database.execute(
                "SELECT * FROM barbershops WHERE email = ?",
                ("demobarber@teste.com",),
            ).fetchone()
            self.assertIsNotNone(shop)
            self.assertEqual(shop["location_is_approximate"], 1)
            self.assertEqual(
                [
                    tuple(row)
                    for row in database.execute(
                        """
                        SELECT name, price_cents FROM services
                        WHERE shop_id = ? ORDER BY price_cents
                        """,
                        (shop["id"],),
                    ).fetchall()
                ],
                [
                    ("Barba", 2500),
                    ("Corte tradicional", 3500),
                    ("Degradê", 4500),
                    ("Corte + barba", 6000),
                ],
            )
            barber = database.execute(
                "SELECT id FROM barbers WHERE shop_id = ?",
                (shop["id"],),
            ).fetchone()
            second_barber_id = database.execute(
                """
                INSERT INTO barbers (shop_id, full_name, phone, specialty, status)
                VALUES (?, ?, ?, ?, 'active')
                """,
                (shop["id"], "Segundo barbeiro", "87999990000", "Cortes"),
            ).lastrowid
            service = database.execute(
                "SELECT id FROM services WHERE shop_id = ? ORDER BY id LIMIT 1",
                (shop["id"],),
            ).fetchone()

            other_owner_id = database.execute(
                """
                INSERT INTO users (full_name, email, password_hash, role)
                VALUES (?, ?, ?, 'business_owner')
                """,
                (
                    "Outro responsável",
                    "outro@teste.com",
                    "not-a-login-hash",
                ),
            ).lastrowid
            other_shop_id = database.execute(
                """
                INSERT INTO barbershops
                    (owner_user_id, business_name, phone, email, address, latitude, longitude)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    other_owner_id,
                    "Outra barbearia",
                    "11999999999",
                    "outro@teste.com",
                    "Rua de teste, 1",
                    -8.0,
                    -38.0,
                ),
            ).lastrowid
            foreign_service_id = database.execute(
                """
                INSERT INTO services (shop_id, name, price_cents, duration_minutes)
                VALUES (?, ?, ?, ?)
                """,
                (other_shop_id, "Serviço privado", 9900, 60),
            ).lastrowid
            database.commit()

        public_page = self.client.get("/barbearias")
        self.assertIn(b"Barbearia Badboys", public_page.data)
        self.assertIn(b"destination=-7.9913239,-38.300214", public_page.data)
        self.assertIn(b"Ponto aproximado", public_page.data)
        owner_services = self.client.get("/services")
        self.assertIn(b"Corte tradicional", owner_services.data)
        self.assertNotIn(b"Servi\xc3\xa7o privado", owner_services.data)
        denied_edit = self.client.get(f"/services/{foreign_service_id}/edit")
        self.assertEqual(denied_edit.status_code, 302)
        owner_barbers = self.client.get("/barbers")
        self.assertIn(b"Profissional de demonstra\xc3\xa7\xc3\xa3o", owner_barbers.data)
        self.assertNotIn(b"Outro respons\xc3\xa1vel", owner_barbers.data)

        self.client.get("/logout")
        client_registration = self.client.post(
            "/cadastro/cliente",
            data={
                "full_name": "Cliente de teste",
                "email": "cliente.shop@example.com",
                "phone": "11987654321",
                "password": "cliente-seguro-123",
                "password_confirmation": "cliente-seguro-123",
            },
            follow_redirects=False,
        )
        self.assertEqual(client_registration.status_code, 302)
        client_appointments = self.client.get(f"/appointments?shop_id={shop['id']}")
        self.assertIn(b"Barbearia Badboys", client_appointments.data)
        self.assertIn(b"Abrir rota e GPS", client_appointments.data)
        self.assertIn(b"CAT\xc3\x81LOGO EXCLUSIVO", client_appointments.data)
        self.assertIn(b"service-catalog-image", client_appointments.data)
        self.assertIn(b"Corte tradicional", client_appointments.data)
        self.assertIn(b"R$ 35,00", client_appointments.data)
        self.assertNotIn(b"Servi\xc3\xa7o privado", client_appointments.data)
        other_shop_appointments = self.client.get(
            f"/appointments?shop_id={other_shop_id}"
        )
        self.assertIn(b"Servi\xc3\xa7o privado", other_shop_appointments.data)
        self.assertNotIn(b"Corte tradicional", other_shop_appointments.data)
        client_catalog = self.client.get(f"/services?shop_id={shop['id']}")
        self.assertIn(b"service-catalog-image", client_catalog.data)
        self.assertIn(b"R$ 35,00", client_catalog.data)
        self.assertIn(
            f"/appointments?shop_id={shop['id']}&amp;service_id={service['id']}".encode(),
            client_catalog.data,
        )

        appointment_date = (date.today() + timedelta(days=1)).isoformat()
        available_before_booking = self.client.get(
            "/appointments/availability",
            query_string={
                "shop_id": shop["id"],
                "barber_id": barber["id"],
                "service_id": service["id"],
                "date": appointment_date,
            },
        )
        self.assertEqual(available_before_booking.status_code, 200)
        slot_before_booking = next(
            slot
            for slot in available_before_booking.json["slots"]
            if slot["time"] == "11:00"
        )
        self.assertTrue(slot_before_booking["available"])

        selected_booking_form = self.client.get(
            f"/appointments?shop_id={shop['id']}&service_id={service['id']}"
        )
        self.assertIn(b"Servi\xc3\xa7o escolhido", selected_booking_form.data)
        self.assertIn(b"Corte tradicional", selected_booking_form.data)
        booking = self.client.post(
            "/appointments/new",
            data={
                "shop_id": str(shop["id"]),
                "barber_id": str(barber["id"]),
                "service_id": str(service["id"]),
                "appointment_date": appointment_date,
                "appointment_time": "11:00",
            },
            follow_redirects=False,
        )
        self.assertEqual(booking.status_code, 302)
        self.assertRegex(
            booking.headers["Location"],
            r"/appointments/\d+/confirmation$",
        )
        confirmation = self.client.get(booking.headers["Location"])
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn(b"Agendamento confirmado!", confirmation.data)
        self.assertIn(b"Barbearia Badboys", confirmation.data)
        self.assertIn(b"Pra\xc3\xa7a Doutor S\xc3\xa9rgio Magalh\xc3\xa3es", confirmation.data)
        self.assertIn(
            f"/barbearias?shop_id={shop['id']}#barbershopMap".encode(),
            confirmation.data,
        )

        occupied_slot = self.client.get(
            "/appointments/availability",
            query_string={
                "shop_id": shop["id"],
                "barber_id": barber["id"],
                "service_id": service["id"],
                "date": appointment_date,
            },
        )
        self.assertFalse(
            next(
                slot
                for slot in occupied_slot.json["slots"]
                if slot["time"] == "11:00"
            )["available"]
        )
        other_barber_slot = self.client.get(
            "/appointments/availability",
            query_string={
                "shop_id": shop["id"],
                "barber_id": second_barber_id,
                "service_id": service["id"],
                "date": appointment_date,
            },
        )
        self.assertTrue(
            next(
                slot
                for slot in other_barber_slot.json["slots"]
                if slot["time"] == "11:00"
            )["available"]
        )

        with self.app.app_context():
            appointment = get_db().execute(
                "SELECT shop_id FROM appointments"
            ).fetchone()
            self.assertEqual(appointment["shop_id"], shop["id"])

        self.client.get("/logout")
        owner_login = self.client.post(
            "/login",
            data={
                "email": "demobarber@teste.com",
                "password": "senha-temporaria-123",
            },
            follow_redirects=False,
        )
        self.assertEqual(owner_login.status_code, 302)
        owner_appointments = self.client.get("/appointments")
        self.assertIn(b"Cliente de teste", owner_appointments.data)
        self.assertIn(b"Corte tradicional", owner_appointments.data)

    def test_legacy_user_role_migration_preserves_foreign_keys(self):
        legacy_path = Path(self.temporary_directory.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'employee')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
            );
            INSERT INTO users (full_name, email, password_hash, role)
            VALUES ('Admin legado', 'legacy@example.com', 'hash', 'admin');
            INSERT INTO clients (full_name, phone, user_id)
            VALUES ('Cliente legado', '11999999999', 1);
            """
        )
        connection.close()

        legacy_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE": str(legacy_path),
            }
        )
        with legacy_app.app_context():
            from banco_de_dados.db import get_db

            database = get_db()
            database.execute(
                """
                INSERT INTO users (full_name, email, password_hash, role)
                VALUES ('Dono', 'owner@example.com', 'hash', 'business_owner')
                """
            )
            database.commit()
            self.assertEqual(
                database.execute(
                    "SELECT full_name FROM clients WHERE id = 1"
                ).fetchone()["full_name"],
                "Cliente legado",
            )
            self.assertEqual(
                database.execute("PRAGMA foreign_key_check").fetchall(),
                [],
            )

    def test_service_photos_can_be_uploaded_previewed_and_served_safely(self):
        from banco_de_dados.db import get_db

        self.client.post(
            "/login",
            data={"email": "admin@barber.local", "password": "admin123"},
        )
        service_form = self.client.get("/services/new")
        self.assertIn(b'enctype="multipart/form-data"', service_form.data)
        self.assertIn(b'name="image_file"', service_form.data)

        image_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/rZsAAAAASUVORK5CYII="
        )
        response = self.client.post(
            "/services/new",
            data={
                "name": "Corte com foto",
                "description": "Corte demonstrativo",
                "price_cents": "3500",
                "duration_minutes": "30",
                "status": "active",
                "image_file": (io.BytesIO(image_data), "corte.png", "image/png"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            service = get_db().execute(
                "SELECT id, image_url FROM services WHERE name = ?",
                ("Corte com foto",),
            ).fetchone()
            self.assertIsNotNone(service)
            image_url = service["image_url"]
        self.assertTrue(image_url.startswith("/catalogo/imagens/"))

        image_response = self.client.get(image_url)
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.mimetype, "image/png")
        self.assertEqual(image_response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(image_response.data, image_data)
        image_response.close()

        edit_response = self.client.post(
            f"/services/{service['id']}/edit",
            data={
                "name": "Corte com foto atualizado",
                "description": "Descrição atualizada",
                "price_cents": "4000",
                "duration_minutes": "35",
                "status": "active",
                "image_url": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(edit_response.status_code, 302)
        with self.app.app_context():
            saved_url = get_db().execute(
                "SELECT image_url FROM services WHERE id = ?",
                (service["id"],),
            ).fetchone()["image_url"]
            self.assertEqual(saved_url, image_url)

        listing = self.client.get("/services")
        self.assertIn(b"service-table-image", listing.data)
        self.assertIn(image_url.encode(), listing.data)

        invalid_response = self.client.post(
            "/services/new",
            data={
                "name": "Arquivo inválido",
                "price_cents": "3500",
                "duration_minutes": "30",
                "image_file": (io.BytesIO(b"not an image"), "fake.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertIn(b"n\xc3\xa3o corresponde a uma imagem", invalid_response.data)
        with self.app.app_context():
            self.assertEqual(
                get_db().execute(
                    "SELECT COUNT(*) FROM services WHERE name = ?",
                    ("Arquivo inválido",),
                ).fetchone()[0],
                0,
            )

        image_path = (
            Path(self.app.instance_path)
            / "uploads"
            / "catalogo"
            / image_url.rsplit("/", 1)[-1]
        )
        image_path.unlink(missing_ok=True)

    def test_business_owner_can_create_appointment_for_any_client(self):
        from banco_de_dados.db import get_db

        register_response = self.client.post(
            "/cadastro/barbearia",
            data={
                "full_name": "Dono Teste",
                "business_name": "Barbearia do Dono",
                "email": "dono@barbearia.local",
                "phone": "87999990001",
                "address": "Rua Central, 10, Serra Talhada",
                "latitude": "-7.9868",
                "longitude": "-38.2920",
                "password": "senha12345",
                "password_confirmation": "senha12345",
            },
            follow_redirects=True,
        )
        self.assertEqual(register_response.status_code, 200)

        with self.app.app_context():
            db = get_db()
            barber = db.execute("SELECT id FROM barbers WHERE full_name = 'Profissional de demonstração'").fetchone()
            service = db.execute("SELECT id FROM services WHERE name = 'Corte tradicional'").fetchone()
            client = db.execute("SELECT id FROM clients WHERE email = 'cliente@barber.local'").fetchone()

        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        starts_at = f"{tomorrow}T10:00"

        booking_response = self.client.post(
            "/appointments/new",
            data={
                "client_id": client["id"],
                "barber_id": barber["id"],
                "service_id": service["id"],
                "starts_at": starts_at,
                "status": "scheduled",
            },
            follow_redirects=True,
        )
        self.assertEqual(booking_response.status_code, 200)
        self.assertIn(b"Agendamento realizado com sucesso", booking_response.data)

        with self.app.app_context():
            db = get_db()
            saved = db.execute(
                "SELECT * FROM appointments WHERE client_id = ? AND barber_id = ?",
                (client["id"], barber["id"]),
            ).fetchone()
            self.assertIsNotNone(saved)
            self.assertEqual(saved["price_cents"], 3500)

    def test_barber_profile_photo_upload_edit_and_remove(self):
        with self.app.app_context():
            from banco_de_dados.db import create_default_admin, get_db, init_db

            init_db()
            create_default_admin()

        self.client.post(
            "/login",
            data={"email": "admin@barber.local", "password": "admin123"},
            follow_redirects=False,
        )

        create_resp = self.client.post(
            "/barbers/new",
            data={
                "full_name": "Barbeiro Estiloso",
                "phone": "11999998888",
                "specialty": "Degradê e barba",
                "status": "active",
                "profile_photo_url": "https://example.com/avatar.jpg",
            },
            follow_redirects=True,
        )
        self.assertEqual(create_resp.status_code, 200)
        self.assertIn("Barbeiro Estiloso", create_resp.get_data(as_text=True))
        self.assertIn("https://example.com/avatar.jpg", create_resp.get_data(as_text=True))

        with self.app.app_context():
            db = get_db()
            barber = db.execute(
                "SELECT * FROM barbers WHERE full_name = ?",
                ("Barbeiro Estiloso",),
            ).fetchone()
            self.assertIsNotNone(barber)
            barber_id = barber["id"]
            self.assertEqual(barber["profile_photo_url"], "https://example.com/avatar.jpg")

        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        edit_resp = self.client.post(
            f"/barbers/{barber_id}/edit",
            data={
                "full_name": "Barbeiro Estiloso Atualizado",
                "phone": "11999998888",
                "specialty": "Degradê e barba",
                "status": "active",
                "profile_photo_url": "",
                "photo_file": (io.BytesIO(png_data), "foto.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(edit_resp.status_code, 200)

        with self.app.app_context():
            db = get_db()
            updated = db.execute(
                "SELECT * FROM barbers WHERE id = ?",
                (barber_id,),
            ).fetchone()
            self.assertTrue(updated["profile_photo_url"].startswith("/barbeiros/imagens/"))
            image_url = updated["profile_photo_url"]

        img_resp = self.client.get(image_url)
        self.assertEqual(img_resp.status_code, 200)

        remove_resp = self.client.post(
            f"/barbers/{barber_id}/edit",
            data={
                "full_name": "Barbeiro Estiloso Atualizado",
                "phone": "11999998888",
                "specialty": "Degradê e barba",
                "status": "active",
                "profile_photo_url": "",
                "remove_photo": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(remove_resp.status_code, 200)

        with self.app.app_context():
            db = get_db()
            cleared = db.execute(
                "SELECT * FROM barbers WHERE id = ?",
                (barber_id,),
            ).fetchone()
            self.assertIsNone(cleared["profile_photo_url"])

    def test_barbershop_profile_barber_of_month_and_internal_ranking(self):
        with self.app.app_context():
            from banco_de_dados.db import create_default_admin, get_db, init_db

            init_db()
            create_default_admin()
            db = get_db()
            owner_id = db.execute(
                """
                INSERT INTO users (full_name, email, password_hash, role)
                VALUES (?, ?, ?, 'business_owner')
                """,
                ("Dono Teste", "dono.teste@barber.local", "hash123"),
            ).lastrowid
            shop_id = db.execute(
                """
                INSERT INTO barbershops (owner_user_id, business_name, phone, email, address, latitude, longitude)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (owner_id, "Barbearia Real", "87999990000", "real@barber.local", "Rua Centro, 100", -7.99, -38.30),
            ).lastrowid

            b1_id = db.execute(
                """
                INSERT INTO barbers (shop_id, full_name, phone, specialty, status)
                VALUES (?, ?, ?, ?, 'active')
                """,
                (shop_id, "Barbeiro Campeão", "87999991111", "Corte degradê"),
            ).lastrowid
            b2_id = db.execute(
                """
                INSERT INTO barbers (shop_id, full_name, phone, specialty, status)
                VALUES (?, ?, ?, ?, 'active')
                """,
                (shop_id, "Barbeiro Segundo", "87999992222", "Barba terapia"),
            ).lastrowid

            other_owner = db.execute(
                """
                INSERT INTO users (full_name, email, password_hash, role)
                VALUES (?, ?, ?, 'business_owner')
                """,
                ("Outro Dono", "outro.dono@barber.local", "hash123"),
            ).lastrowid
            other_shop = db.execute(
                """
                INSERT INTO barbershops (owner_user_id, business_name, phone, email, address, latitude, longitude)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (other_owner, "Outra Barbearia", "87999993333", "outro@barber.local", "Rua Outra, 200", -7.98, -38.29),
            ).lastrowid
            db.execute(
                """
                INSERT INTO barbers (shop_id, full_name, phone, specialty, status)
                VALUES (?, ?, ?, ?, 'active')
                """,
                (other_shop, "Barbeiro Estranho", "87999994444", "Outro serviço"),
            )

            service_id = db.execute(
                """
                INSERT INTO services (shop_id, name, price_cents, duration_minutes, status)
                VALUES (?, ?, ?, ?, 'active')
                """,
                (shop_id, "Corte Vip", 4500, 30),
            ).lastrowid
            client_id = db.execute(
                """
                INSERT INTO clients (full_name, phone)
                VALUES (?, ?)
                """,
                ("Cliente Feliz", "87988887777"),
            ).lastrowid
            current_month = date.today().strftime("%Y-%m")
            db.execute(
                """
                INSERT INTO appointments (shop_id, client_id, barber_id, service_id, starts_at, ends_at, price_cents, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed')
                """,
                (shop_id, client_id, b1_id, service_id, f"{current_month}-10 10:00:00", f"{current_month}-10 10:30:00", 4500),
            )
            db.commit()

        resp = self.client.get(f"/barbearias/{shop_id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Barbearia Real", html)
        self.assertIn("Barbeiro Campeão", html)
        self.assertIn("Barbeiro do Mês", html)
        self.assertIn("Barbeiro Segundo", html)
        self.assertNotIn("Barbeiro Estranho", html)
        self.assertNotIn("atendimento(s) neste mês", html)

        list_resp = self.client.get("/barbearias")
        self.assertEqual(list_resp.status_code, 200)
        list_html = list_resp.get_data(as_text=True)
        self.assertIn(f"/barbearias/{shop_id}", list_html)
        self.assertIn("Barbeiro do mês: Barbeiro Campeão", list_html)


if __name__ == "__main__":
    unittest.main()
