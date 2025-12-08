# escola_routes.py (VERSÃO ATUALIZADA)

from flask import Blueprint, render_template, request, flash, redirect, url_for
from models import Escola
from extensions import db
from utils import role_required

escola_bp = Blueprint('escola', __name__, url_prefix='/escolas')

@escola_bp.route('/')
@role_required('admin', 'RH')
def listar_escolas():
    escolas = Escola.query.order_by(Escola.nome).all()
    return render_template('escolas.html', escolas=escolas)

@escola_bp.route('/nova', methods=['POST'])
@role_required('admin', 'RH')
def nova_escola():
    nome = request.form.get('nome')
    codigo_inep = request.form.get('codigo_inep') # <-- CAMPO NOVO
    latitude = request.form.get('latitude')
    longitude = request.form.get('longitude')

    if not nome:
        flash('O nome da escola é obrigatório.', 'danger')
        return redirect(url_for('escola.listar_escolas'))

    # Verifica se o código INEP já existe
    if codigo_inep and Escola.query.filter_by(codigo_inep=codigo_inep).first():
        flash(f'O Código INEP "{codigo_inep}" já está em uso por outra escola.', 'danger')
        return redirect(url_for('escola.listar_escolas'))

    nova = Escola(
        nome=nome,
        codigo_inep=codigo_inep, # <-- CAMPO NOVO
        latitude=latitude if latitude else None,
        longitude=longitude if longitude else None
    )
    db.session.add(nova)
    db.session.commit()
    flash('Escola cadastrada com sucesso!', 'success')
    return redirect(url_for('escola.listar_escolas'))

@escola_bp.route('/editar/<int:id>', methods=['POST'])
@role_required('admin', 'RH')
def editar_escola(id):
    escola = Escola.query.get_or_404(id)
    
    nome = request.form.get('nome')
    codigo_inep = request.form.get('codigo_inep') # <-- CAMPO NOVO
    latitude = request.form.get('latitude')
    longitude = request.form.get('longitude')

    if not nome:
        flash('O nome da escola é obrigatório.', 'danger')
        return redirect(url_for('escola.listar_escolas'))
    
    # Verifica se o código INEP já está em uso por OUTRA escola
    if codigo_inep:
        escola_existente = Escola.query.filter(Escola.codigo_inep == codigo_inep, Escola.id != id).first()
        if escola_existente:
            flash(f'O Código INEP "{codigo_inep}" já está em uso por outra escola.', 'danger')
            return redirect(url_for('escola.listar_escolas'))

    escola.nome = nome
    escola.codigo_inep = codigo_inep # <-- CAMPO NOVO
    escola.latitude = latitude if latitude else None
    escola.longitude = longitude if longitude else None
    
    db.session.commit()
    flash('Dados da escola atualizados com sucesso!', 'success')
    return redirect(url_for('escola.listar_escolas'))