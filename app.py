from pathlib import Path

from flask import Flask
from flask_login import LoginManager

from config import Config
from banco_de_dados.db import close_db, create_default_users, init_db
from rotas.main import main_bp


login_manager = LoginManager()


@login_manager.user_loader
def load_user(user_id):
    from modelos.user import get_user_by_id

    return get_user_by_id(user_id)


def create_app(test_config=None):
    project_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        instance_path=str(project_dir / "dados"),
        instance_relative_config=True,
        template_folder="paginas",
        static_folder="arquivos_estaticos",
        static_url_path="/static",
    )
    app.config.from_object(Config)

    if test_config is not None:
        app.config.update(test_config)

    if not app.config["SECRET_KEY"] and not app.testing:
        app.config["SECRET_KEY"] = "dev-barber-manager-secret"

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()
        create_default_users()

    login_manager.init_app(app)
    login_manager.login_view = "main.login"

    app.register_blueprint(main_bp)

    @app.errorhandler(413)
    def file_too_large(error):
        return "A imagem excede o limite de 5 MB.", 413

    @app.cli.command("init-db")
    def initialize_database():
        """Create the database tables."""
        init_db()
        create_default_users()
        print("Banco de dados inicializado.")

    return app


if __name__ == "__main__":
    create_app().run()
