import json
from flask import Blueprint, render_template
from sqlalchemy import func
from models import Escola, Ponto, Servidor
from utils import login_required
from datetime import date, timedelta
from extensions import db, bcrypt
from utils import role_required

frequencia_bp = Blueprint('frequencia', __name__)

@frequencia_bp.route('/frequencia/dashboard')
@login_required
@role_required('RH', 'admin')
def dashboard_frequencia():
    # --- DADOS PARA O MAPA ---
    escolas_com_coords = Escola.query.filter(Escola.latitude.isnot(None), Escola.longitude.isnot(None)).all()
    escolas_json = json.dumps([
        {'nome': e.nome, 'lat': e.latitude, 'lon': e.longitude} for e in escolas_com_coords
    ])

    # --- DADOS PARA O GRÁFICO DE PONTOS DIÁRIOS (ÚLTIMOS 7 DIAS) ---
    hoje = date.today()
    data_inicio = hoje - timedelta(days=6)
    
    pontos_por_dia = db.session.query(
        func.date(Ponto.timestamp).label('dia'),
        func.count(Ponto.id).label('total')
    ).filter(
        func.date(Ponto.timestamp) >= data_inicio
    ).group_by('dia').order_by('dia').all()
    
    # Prepara os dados para o Chart.js
    labels_dias = [(data_inicio + timedelta(days=i)).strftime('%d/%m') for i in range(7)]
    dados_dias = [0] * 7
    for registro in pontos_por_dia:
        ano, mes, dia = registro.dia.split('-')
        dia_str = f"{dia}/{mes}"
        if dia_str in labels_dias:
            idx = labels_dias.index(dia_str)
            dados_dias[idx] = registro.total

    # --- LÓGICA PARA SERVIDORES FALTOSOS HOJE ---
    # Atenção: Esta lógica assume que o campo 'lotacao' do Servidor corresponde ao 'nome' da Escola.
    servidores_presentes_cpf = [r.servidor_cpf for r in db.session.query(Ponto.servidor_cpf).filter(func.date(Ponto.timestamp) == hoje).distinct()]
    
    # Busca servidores que não estão na lista de presentes
    servidores_faltosos = Servidor.query.filter(
        Servidor.cpf.isnot(None),
        Servidor.cpf.notin_(servidores_presentes_cpf)
    ).all()
    
    # Agrupa os faltosos por lotação (escola)
    faltosos_por_escola = {}
    for servidor in servidores_faltosos:
        lotacao = servidor.lotacao or "Sem Lotação"
        faltosos_por_escola[lotacao] = faltosos_por_escola.get(lotacao, 0) + 1


    return render_template(
        'frequencia_dashboard.html',
        escolas_json=escolas_json,
        labels_dias=json.dumps(labels_dias),
        dados_dias=json.dumps(dados_dias),
        faltosos_por_escola=faltosos_por_escola
    )
