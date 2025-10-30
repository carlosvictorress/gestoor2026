from dotenv import load_dotenv
import os

# Carrega as variáveis de ambiente do ficheiro .env
# É importante que isto aconteça antes de importar a 'app'
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)

# Agora importa a aplicação
from .app import app

if __name__ == "__main__":
    app.run()
