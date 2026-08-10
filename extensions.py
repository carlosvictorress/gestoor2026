# extensions.py
# Este arquivo vai guardar as extensões do Flask para evitar importações circulares.

from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from sqlalchemy import event
from sqlalchemy.engine import Engine

# Apenas criamos as variáveis aqui, sem conectar ao 'app'
db = SQLAlchemy()
bcrypt = Bcrypt()

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if type(dbapi_connection).__module__ in ("sqlite3", "pysqlite2.dbapi2"):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA cache_size=-64000;")
        except Exception:
            pass
        finally:
            cursor.close()

