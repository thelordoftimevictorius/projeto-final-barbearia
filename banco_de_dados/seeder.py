import sqlite3
from werkzeug.security import generate_password_hash


def seed_demo_data(database):
    """
    Popula o banco de dados com os dados de demonstração da Barbearia Badboys,
    barbeiros, serviços e contas caso o banco esteja novo/vazio.
    """
    owner_pwd_hash = generate_password_hash("demobarber123")
    client_pwd_hash = generate_password_hash("12345678")

    # 1. Usuários Donos de Barbearia
    owners = [
        ("demobarber@teste.com", "Beiçola", "business_owner", owner_pwd_hash),
        ("barbearia.vintage@demo.com", "João Pereira", "business_owner", owner_pwd_hash),
        ("noir.barber@demo.com", "Carlos Mendes", "business_owner", owner_pwd_hash),
        ("mestre.tesoura@demo.com", "Rafael Costa", "business_owner", owner_pwd_hash),
        ("barba.blade@demo.com", "Eduardo Silva", "business_owner", owner_pwd_hash),
        ("the.groom@demo.com", "Vinícius Rocha", "business_owner", owner_pwd_hash),
    ]

    owner_ids = {}
    for email, name, role, pwd in owners:
        user = database.execute(
            "SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
        if user is None:
            cur = database.execute(
                """
                INSERT INTO users (full_name, email, password_hash, role, is_active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (name, email, pwd, role),
            )
            owner_ids[email] = cur.lastrowid
        else:
            owner_ids[email] = user["id"]

    # Usuário Emmanuel Salazar (Cliente)
    emmanuel = database.execute(
        "SELECT id FROM users WHERE email = 'emmanuelsalazar@gmail.com' COLLATE NOCASE"
    ).fetchone()
    if emmanuel is None:
        cur = database.execute(
            """
            INSERT INTO users (full_name, email, password_hash, role, is_active)
            VALUES (?, ?, ?, 'client', 1)
            """,
            ("Emmanuel Salazar", "emmanuelsalazar@gmail.com", client_pwd_hash),
        )
        emmanuel_user_id = cur.lastrowid
    else:
        emmanuel_user_id = emmanuel["id"]

    client_record = database.execute(
        "SELECT id FROM clients WHERE email = 'emmanuelsalazar@gmail.com' COLLATE NOCASE"
    ).fetchone()
    if client_record is None:
        database.execute(
            """
            INSERT INTO clients (full_name, phone, email, user_id)
            VALUES ('Emmanuel Salazar', '8796417792', 'emmanuelsalazar@gmail.com', ?)
            """,
            (emmanuel_user_id,),
        )

    # 2. Barbearias
    shops_data = [
        (
            owner_ids["demobarber@teste.com"],
            "Barbearia Badboys",
            "87999786141",
            "demobarber@teste.com",
            "Praça Doutor Sérgio Magalhães, Nossa Senhora da Penha, Serra Talhada - PE",
            -7.9913239,
            -38.300214,
            1,
        ),
        (
            owner_ids["barbearia.vintage@demo.com"],
            "Barbearia Vintage",
            "11987654301",
            "barbearia.vintage@demo.com",
            "Rua Cornélio Soares, s/n, Nossa Senhora da Penha, Serra Talhada - PE, 56903-510",
            -7.9922948,
            -38.3006815,
            1,
        ),
        (
            owner_ids["noir.barber@demo.com"],
            "Noir Barber Club",
            "11976543210",
            "noir.barber@demo.com",
            "Avenida Afonso Magalhães, s/n, São Cristóvão, Serra Talhada - PE, 56903-590",
            -7.9891864,
            -38.2914801,
            1,
        ),
        (
            owner_ids["mestre.tesoura@demo.com"],
            "Mestre do Tesoura",
            "11965432109",
            "mestre.tesoura@demo.com",
            "Rua José Alves da Silveira, s/n, Nossa Senhora da Conceição, Serra Talhada - PE, 56903-314",
            -7.989275,
            -38.2796942,
            1,
        ),
        (
            owner_ids["barba.blade@demo.com"],
            "Barba & Blade",
            "11954321098",
            "barba.blade@demo.com",
            "Rua Antônio Inácio de Medeiros, s/n, São Cristóvão, Serra Talhada - PE, 56912-380",
            -7.985418,
            -38.2884523,
            1,
        ),
        (
            owner_ids["the.groom@demo.com"],
            "The Groom Studio",
            "11943210987",
            "the.groom@demo.com",
            "Avenida Miguel Nunes de Souza, s/n, São Cristóvão, Serra Talhada - PE, 56903-120",
            -7.9849218,
            -38.2952438,
            1,
        ),
    ]

    shop_ids = {}
    for owner_id, b_name, phone, email, address, lat, lng, approx in shops_data:
        shop = database.execute(
            "SELECT id FROM barbershops WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
        if shop is None:
            cur = database.execute(
                """
                INSERT INTO barbershops (owner_user_id, business_name, phone, email, address, latitude, longitude, location_is_approximate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (owner_id, b_name, phone, email, address, lat, lng, approx),
            )
            shop_ids[b_name] = cur.lastrowid
        else:
            shop_ids[b_name] = shop["id"]

    badboys_id = shop_ids.get("Barbearia Badboys", 1)

    # 3. Barbeiros da Barbearia Badboys
    barbers_badboys = [
        (
            badboys_id,
            "Agostinho Carrara",
            "171008799",
            "Barba e estética Capilar",
            "/barbeiros/imagens/193026f1c1fe42a193b9d291edc0b707.png",
        ),
        (
            badboys_id,
            "Lineu Silva",
            "0001000",
            "Corte de Cabelo e Barba",
            "/barbeiros/imagens/b67d5f56ce4345eeb12688257f4a786b.png",
        ),
        (
            badboys_id,
            "Dudu Nobre",
            "0800 4004 333",
            "Tratamento para a Alopecia Genética",
            "/barbeiros/imagens/2a47203332214161900031e4cb15185e.png",
        ),
        (
            badboys_id,
            "Tuco Silva",
            "111 111 111",
            "Corte de Cabelos degradê e tratamento de cabelos longos",
            "/barbeiros/imagens/97b0f84ef65741a6924d25b23bf08fc5.png",
        ),
    ]

    for shop_id, name, phone, spec, photo in barbers_badboys:
        exists = database.execute(
            "SELECT 1 FROM barbers WHERE full_name = ? AND shop_id = ?", (name, shop_id)
        ).fetchone()
        if not exists:
            database.execute(
                """
                INSERT INTO barbers (shop_id, full_name, phone, specialty, profile_photo_url, status)
                VALUES (?, ?, ?, ?, ?, 'active')
                """,
                (shop_id, name, phone, spec, photo),
            )

    # 4. Serviços da Barbearia Badboys
    services_badboys = [
        (badboys_id, "Corte Tradicional", "Corte na tesoura e máquina com acabamento impecável.", 3500, 30),
        (badboys_id, "Barba Completa", "Barba esculpida com toalha quente e óleos essenciais.", 2500, 20),
        (badboys_id, "Combo Corte + Barba", "Corte de cabelo e barba completa com finalização VIP.", 5500, 45),
        (badboys_id, "Tratamento Capilar VIP", "Lavagem especial, hidratação profunda e massagem capilar.", 6000, 40),
    ]

    for shop_id, name, desc, price, dur in services_badboys:
        exists = database.execute(
            "SELECT 1 FROM services WHERE name = ? AND shop_id = ?", (name, shop_id)
        ).fetchone()
        if not exists:
            database.execute(
                """
                INSERT INTO services (shop_id, name, description, price_cents, duration_minutes, status)
                VALUES (?, ?, ?, ?, ?, 'active')
                """,
                (shop_id, name, desc, price, dur),
            )

    database.commit()
