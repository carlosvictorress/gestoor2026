from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from extensions import db
from models import ChamadoTecnico, RelatorioTecnico, Servidor
from utils import login_required, role_required
from flask import jsonify
from sqlalchemy import func
from models import Escola

helpdesk_bp = Blueprint('helpdesk', __name__)

from models import Escola # Certifique-se de que Escola está importada

@helpdesk_bp.route('/painel-chamados')
@login_required
@role_required('admin')
def painel_chamados():
    chamados = ChamadoTecnico.query.order_by(ChamadoTecnico.data_abertura.desc()).all()
    
    # Criamos um dicionário com ID: NOME de todas as escolas
    # Isso evita o erro de atributo no HTML
    escolas = {e.id: e.nome for e in Escola.query.all()}
    
    total_abertos = ChamadoTecnico.query.filter_by(status='Aberto').count()
    total_andamento = ChamadoTecnico.query.filter_by(status='Em Andamento').count()
    
    return render_template('painel_chamados.html', 
                           chamados=chamados, 
                           abertos=total_abertos, 
                           andamento=total_andamento,
                           lista_escolas=escolas) # Passamos o dicionário aqui

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
    cpf_limpo = ''.join(filter(str.isdigit, cpf))
    
    # Busca o servidor pelo CPF
    servidor = Servidor.query.filter_by(cpf=cpf_limpo).first()

    if servidor:
        nome_escola = "Secretaria / Sem Vínculo"
        # Se o servidor tiver um escola_id, buscamos o nome da escola manualmente
        if servidor.escola_id:
            escola = Escola.query.get(servidor.escola_id)
            if escola:
                nome_escola = escola.nome

        return jsonify({
            "sucesso": True,
            "nome": servidor.nome,
            "escola": nome_escola,
            "id_escola": servidor.escola_id or 0
        })
    
    return jsonify({"sucesso": False, "mensagem": "CPF não localizado"}), 404

@helpdesk_bp.route('/chamado/<int:id>')
@login_required
@role_required('admin')
def detalhes_chamado(id):
    chamado = ChamadoTecnico.query.get_or_404(id)
    
    # Busca o nome do servidor manualmente pelo CPF do chamado
    servidor = Servidor.query.filter_by(cpf=chamado.solicitante_cpf).first()
    nome_solicitante = servidor.nome if servidor else "Não Identificado"
    
    # Busca o nome da escola
    escola = Escola.query.get(chamado.escola_id)
    nome_escola = escola.nome if escola else "Secretaria"

    return render_template('detalhes_chamado.html', 
                           chamado=chamado, 
                           solicitante=nome_solicitante,
                           escola=nome_escola)