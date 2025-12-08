from flask import Blueprint, render_template, session
from utils import login_required, role_required
from datetime import datetime

# 1. Cria o Blueprint para o módulo de Prestação de Contas
contas_bp = Blueprint('contas', __name__, url_prefix='/contas')

@contas_bp.route('/')
@login_required
@role_required(['Gestor', 'Administrador', 'Fiscal']) # AJUSTE AQUI OS PAPÉIS QUE PODEM ACESSAR
def dashboard():
    """Renderiza a página central de relatórios com a lógica de carregamento de 20s."""
    return render_template('contas_dashboard.html', now=datetime.now())

# Adicione outras rotas de relatórios aqui se necessário.