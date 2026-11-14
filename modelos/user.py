from flask_login import UserMixin
from werkzeug.security import check_password_hash

from banco_de_dados.db import get_db


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.full_name = row["full_name"]
        self.email = row["email"]
        self.password_hash = row["password_hash"]
        self.role = row["role"]
        self.is_active_flag = bool(row["is_active"])

    @property
    def is_active(self):
        return self.is_active_flag

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @classmethod
    def from_row(cls, row):
        if row is None:
            return None
        return cls(row)


def get_user_by_email(email):
    row = get_db().execute(
        "SELECT * FROM users WHERE email = ? COLLATE NOCASE",
        (email,),
    ).fetchone()
    return User.from_row(row)


def get_user_by_id(user_id):
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User.from_row(row)


def authenticate_user(email, password):
    user = get_user_by_email(email)
    if user is None or not user.check_password(password):
        return None
    return user if user.is_active else None
