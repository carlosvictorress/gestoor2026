from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from extensions import db
from models import ChamadoTecnico, RelatorioTecnico, Servidor
from utils import login_required, role_required
from flask import jsonify

helpdesk_bp = Blueprint('helpdesk', __name__)

@helpdesk_bp.route('/painel-chamados')
@login_required
@role_required('admin')
def painel_chamados():
    # Busca chamados abertos e em andamento primeiro
    chamados = ChamadoTecnico.query.order_by(ChamadoTecnico.data_abertura.desc()).all()
    
    # Contadores para o dashboard
    total_abertos = ChamadoTecnico.query.filter_by(status='Aberto').count()
    total_andamento = ChamadoTecnico.query.filter_by(status='Em Andamento').count()
    
    return render_template('painel_chamados.html', chamados=chamados, abertos=total_abertos, andamento=total_andamento)

@helpdesk_bp.route('/suportetecnico')
def suporte_externo():
    # Esta rota é pública, por isso não tem @login_required
    return render_template('suporte_externo.html')

@helpdesk_bp.route('/abrir-chamado', methods=['POST'])
def abrir_chamado():
    cpf = request.form.get('cpf_servidor')
    escola_id = request.form.get('id_escola')
    categoria = request.form.get('categoria')
    patrimonio = request.form.get('patrimonio')
    descricao = request.form.get('descricao')

    novo_chamado = ChamadoTecnico(
        solicitante_cpf=cpf,
        escola_id=escola_id,
        categoria=categoria,
        patrimonio_id=patrimonio,
        descricao_problema=descricao,
        status='Aberto'
    )

    db.session.add(novo_chamado)
    db.session.commit()
    
    flash("Chamado enviado com sucesso para a TI!", "success")
    return redirect(url_for('helpdesk.suporte_externo'))

@helpdesk_bp.route('/api/buscar-servidor/<cpf>')
def buscar_servidor_api(cpf):
    # 1. Limpa o CPF (deixa só números)
    cpf_limpo = ''.join(filter(str.isdigit, cpf))
    
    # 2. Busca no banco de dados de Valença do Piauí
    servidor = Servidor.query.filter_by(cpf=cpf_limpo).first()

    if servidor:
        # 3. Se achou, devolve os dados para o JavaScript
        return jsonify({
            "sucesso": True,
            "nome": servidor.nome,
            "escola": servidor.escola.nome if servidor.escola else "Secretaria",
            "id_escola": servidor.escola_id
        })
    
    # 4. Se não achou, avisa o erro
    return jsonify({"sucesso": False, "mensagem": "CPF não localizado"}), 404
