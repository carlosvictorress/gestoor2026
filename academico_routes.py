from flask import Blueprint, render_template, request, flash, redirect, url_for, session
from models import (
    AcadAluno, Escola, AcadTurma, AcadMatricula, 
    AcadDisciplina, AcadPeriodo, Servidor, 
    acad_turma_disciplinas_professores, AcadNota
)
from extensions import db
from utils import role_required
from datetime import datetime
import io
import csv
import base64
import qrcode
import xml.etree.ElementTree as ET

academico_bp = Blueprint('academico', __name__, url_prefix='/academico')

@academico_bp.route('/')
@role_required('admin', 'academico', 'RH')
def dashboard():
    total_alunos = AcadAluno.query.count()
    return render_template('academico/dashboard.html', total_alunos=total_alunos)

@academico_bp.route('/alunos', methods=['GET', 'POST'])
@role_required('admin', 'academico', 'RH')
def gerenciar_alunos():
    if request.method == 'POST': # Adicionar novo aluno
        try:
            data_nascimento_str = request.form.get('data_nascimento')
            data_nascimento = datetime.strptime(data_nascimento_str, '%Y-%m-%d').date() if data_nascimento_str else None

            novo_aluno = AcadAluno(
                nome_completo=request.form.get('nome_completo'),
                data_nascimento=data_nascimento,
                cpf=request.form.get('cpf'),
                sexo=request.form.get('sexo'),
                cor_raca=request.form.get('cor_raca'),
                filiacao_1=request.form.get('filiacao_1'),
                filiacao_2=request.form.get('filiacao_2'),
                nome_responsavel=request.form.get('nome_responsavel'),
                telefone_responsavel=request.form.get('telefone_responsavel'),
                endereco=request.form.get('endereco'),
                necessidade_especial='necessidade_especial' in request.form,
                tipo_necessidade=request.form.get('tipo_necessidade')
            )
            db.session.add(novo_aluno)
            db.session.commit()
            flash('Aluno cadastrado com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar aluno: {e}', 'danger')
        return redirect(url_for('academico.gerenciar_alunos'))

    alunos = AcadAluno.query.order_by(AcadAluno.nome_completo).all()
    return render_template('academico/alunos.html', alunos=alunos)

@academico_bp.route('/alunos/editar/<int:aluno_id>', methods=['POST'])
@role_required('admin', 'academico', 'RH')
def editar_aluno(aluno_id):
    aluno = AcadAluno.query.get_or_404(aluno_id)
    try:
        data_nascimento_str = request.form.get('data_nascimento')
        aluno.nome_completo = request.form.get('nome_completo')
        aluno.data_nascimento = datetime.strptime(data_nascimento_str, '%Y-%m-%d').date() if data_nascimento_str else None
        aluno.cpf = request.form.get('cpf')
        aluno.sexo = request.form.get('sexo')
        aluno.cor_raca = request.form.get('cor_raca')
        aluno.filiacao_1 = request.form.get('filiacao_1')
        aluno.filiacao_2 = request.form.get('filiacao_2')
        aluno.nome_responsavel = request.form.get('nome_responsavel')
        aluno.telefone_responsavel = request.form.get('telefone_responsavel')
        aluno.endereco = request.form.get('endereco')
        aluno.necessidade_especial = 'necessidade_especial' in request.form
        aluno.tipo_necessidade = request.form.get('tipo_necessidade')
        
        db.session.commit()
        flash('Dados do aluno atualizados com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao atualizar dados do aluno: {e}', 'danger')
    return redirect(url_for('academico.gerenciar_alunos'))

@academico_bp.route('/alunos/excluir/<int:aluno_id>', methods=['POST'])
@role_required('admin', 'academico', 'RH')
def excluir_aluno(aluno_id):
    aluno = AcadAluno.query.get_or_404(aluno_id)
    if aluno.matriculas:
        flash('Não é possível excluir um aluno que possui matrículas ativas ou passadas.', 'danger')
        return redirect(url_for('academico.gerenciar_alunos'))
    try:
        db.session.delete(aluno)
        db.session.commit()
        flash('Aluno excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir aluno: {e}', 'danger')
    return redirect(url_for('academico.gerenciar_alunos'))

@academico_bp.route('/turmas', methods=['GET'])
@role_required('admin', 'academico', 'RH')
def gerenciar_turmas():
    ano_selecionado = request.args.get('ano', datetime.now().year, type=int)
    query = AcadTurma.query.filter_by(ano_letivo=ano_selecionado)
    turmas = query.order_by(AcadTurma.escola_id, AcadTurma.nome).all()
    escolas = Escola.query.order_by(Escola.nome).all()
    return render_template('academico/turmas.html', turmas=turmas, escolas=escolas, ano_selecionado=ano_selecionado)

@academico_bp.route('/turmas/nova', methods=['POST'])
@role_required('admin', 'academico', 'RH')
def nova_turma():
    try:
        nova = AcadTurma(
            nome=request.form.get('nome'),
            ano_letivo=request.form.get('ano_letivo', type=int),
            turno=request.form.get('turno'),
            etapa_ensino=request.form.get('etapa_ensino'),
            modalidade=request.form.get('modalidade'),
            vagas=request.form.get('vagas', type=int),
            escola_id=request.form.get('escola_id', type=int)
        )
        db.session.add(nova)
        db.session.commit()
        flash('Turma criada com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao criar turma: {e}', 'danger')
    return redirect(url_for('academico.gerenciar_turmas', ano=request.form.get('ano_letivo')))

@academico_bp.route('/turmas/editar/<int:turma_id>', methods=['POST'])
@role_required('admin', 'academico', 'RH')
def editar_turma(turma_id):
    turma = AcadTurma.query.get_or_404(turma_id)
    try:
        turma.nome = request.form.get('nome')
        turma.ano_letivo = request.form.get('ano_letivo', type=int)
        turma.turno = request.form.get('turno')
        turma.etapa_ensino = request.form.get('etapa_ensino')
        turma.modalidade = request.form.get('modalidade')
        turma.vagas = request.form.get('vagas', type=int)
        turma.escola_id = request.form.get('escola_id', type=int)
        db.session.commit()
        flash('Turma atualizada com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao atualizar turma: {e}', 'danger')
    return redirect(url_for('academico.gerenciar_turmas', ano=turma.ano_letivo))

@academico_bp.route('/turmas/excluir/<int:turma_id>', methods=['POST'])
@role_required('admin', 'academico', 'RH')
def excluir_turma(turma_id):
    turma = AcadTurma.query.get_or_404(turma_id)
    if turma.matriculas:
        flash('Não é possível excluir uma turma que possui alunos matriculados.', 'danger')
        return redirect(url_for('academico.gerenciar_turmas', ano=turma.ano_letivo))
    try:
        db.session.delete(turma)
        db.session.commit()
        flash('Turma excluída com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir turma: {e}', 'danger')
    return redirect(url_for('academico.gerenciar_turmas', ano=turma.ano_letivo))

@academico_bp.route('/turmas/detalhes/<int:turma_id>')
@role_required('admin', 'academico', 'RH')
def detalhes_turma(turma_id):
    turma = AcadTurma.query.get_or_404(turma_id)
    alunos_nao_matriculados = AcadAluno.query.filter(~AcadAluno.matriculas.any(AcadMatricula.turma_id == turma_id)).order_by(AcadAluno.nome_completo).all()
    return render_template('academico/detalhes_turma.html', turma=turma, alunos_para_matricular=alunos_nao_matriculados)

@academico_bp.route('/turmas/<int:turma_id>/matricular', methods=['POST'])
@role_required('admin', 'academico', 'RH')
def matricular_aluno(turma_id):
    turma = AcadTurma.query.get_or_404(turma_id)
    aluno_id = request.form.get('aluno_id', type=int)
    if not aluno_id:
        flash('Nenhum aluno selecionado.', 'danger')
        return redirect(url_for('academico.detalhes_turma', turma_id=turma_id))
    if len(turma.matriculas) >= turma.vagas:
        flash('Não há mais vagas disponíveis nesta turma.', 'danger')
        return redirect(url_for('academico.detalhes_turma', turma_id=turma_id))
    try:
        nova_matricula = AcadMatricula(aluno_id=aluno_id, turma_id=turma_id, data_matricula=datetime.now().date())
        db.session.add(nova_matricula)
        db.session.commit()
        flash('Aluno matriculado na turma com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao matricular aluno: {e}', 'danger')
    return redirect(url_for('academico.detalhes_turma', turma_id=turma_id))

@academico_bp.route('/matricula/cancelar/<int:matricula_id>', methods=['POST'])
@role_required('admin', 'academico', 'RH')
def cancelar_matricula(matricula_id):
    matricula = AcadMatricula.query.get_or_404(matricula_id)
    turma_id = matricula.turma_id
    try:
        db.session.delete(matricula)
        db.session.commit()
        flash('Matrícula cancelada com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao cancelar a matrícula: {e}', 'danger')
    return redirect(url_for('academico.detalhes_turma', turma_id=turma_id))

@academico_bp.route('/configuracoes', methods=['GET', 'POST'])
@role_required('admin', 'academico')
def configuracoes_academicas():
    if request.form.get('form_type') == 'disciplina':
        try:
            nova_disciplina = AcadDisciplina(nome=request.form.get('nome'), area_conhecimento=request.form.get('area_conhecimento'))
            db.session.add(nova_disciplina)
            db.session.commit()
            flash('Disciplina cadastrada com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar disciplina: {e}', 'danger')
        return redirect(url_for('academico.configuracoes_academicas'))
    if request.form.get('form_type') == 'periodo':
        try:
            novo_periodo = AcadPeriodo(nome=request.form.get('nome'), ano_letivo=request.form.get('ano_letivo', type=int))
            db.session.add(novo_periodo)
            db.session.commit()
            flash('Período cadastrado com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar período: {e}', 'danger')
        return redirect(url_for('academico.configuracoes_academicas'))
    disciplinas = AcadDisciplina.query.order_by(AcadDisciplina.nome).all()
    periodos = AcadPeriodo.query.order_by(AcadPeriodo.ano_letivo.desc(), AcadPeriodo.nome).all()
    ano_atual = datetime.now().year
    return render_template('academico/configuracoes.html', disciplinas=disciplinas, periodos=periodos, ano_atual=ano_atual)

@academico_bp.route('/disciplinas/excluir/<int:id>', methods=['POST'])
@role_required('admin', 'academico')
def excluir_disciplina(id):
    disciplina = AcadDisciplina.query.get_or_404(id)
    try:
        db.session.delete(disciplina)
        db.session.commit()
        flash('Disciplina excluída com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir disciplina. Verifique se ela não está em uso. Erro: {e}', 'danger')
    return redirect(url_for('academico.configuracoes_academicas'))

@academico_bp.route('/periodos/excluir/<int:id>', methods=['POST'])
@role_required('admin', 'academico')
def excluir_periodo(id):
    periodo = AcadPeriodo.query.get_or_404(id)
    try:
        db.session.delete(periodo)
        db.session.commit()
        flash('Período excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir período. Verifique se ele não está em uso. Erro: {e}', 'danger')
    return redirect(url_for('academico.configuracoes_academicas'))

@academico_bp.route('/turmas/gerenciar/<int:turma_id>', methods=['GET', 'POST'])
@role_required('admin', 'academico')
def gerenciar_turma_disciplinas(turma_id):
    turma = AcadTurma.query.get_or_404(turma_id)
    if request.method == 'POST':
        try:
            disciplina_id = request.form.get('disciplina_id', type=int)
            professor_num_contrato = request.form.get('professor_num_contrato')
            disciplina = AcadDisciplina.query.get(disciplina_id)
            professor = Servidor.query.get(professor_num_contrato)
            if not disciplina or not professor:
                flash('Disciplina ou professor inválido.', 'danger')
                return redirect(url_for('academico.gerenciar_turma_disciplinas', turma_id=turma.id))
            stmt = db.select(acad_turma_disciplinas_professores).where(db.and_(acad_turma_disciplinas_professores.c.turma_id == turma.id, acad_turma_disciplinas_professores.c.disciplina_id == disciplina.id))
            existe = db.session.execute(stmt).first()
            if existe:
                flash(f'A disciplina "{disciplina.nome}" já está associada a esta turma.', 'warning')
            else:
                stmt = db.insert(acad_turma_disciplinas_professores).values(turma_id=turma.id, disciplina_id=disciplina.id, professor_num_contrato=professor.num_contrato)
                db.session.execute(stmt)
                db.session.commit()
                flash('Professor e disciplina associados à turma com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao associar: {e}', 'danger')
        return redirect(url_for('academico.gerenciar_turma_disciplinas', turma_id=turma.id))
    disciplinas = AcadDisciplina.query.order_by(AcadDisciplina.nome).all()
    professores = Servidor.query.order_by(Servidor.nome).all()
    stmt = db.select(acad_turma_disciplinas_professores.c.disciplina_id, acad_turma_disciplinas_professores.c.professor_num_contrato).where(acad_turma_disciplinas_professores.c.turma_id == turma_id)
    associacoes_raw = db.session.execute(stmt).all()
    associacoes = []
    for disc_id, prof_id in associacoes_raw:
        disciplina = AcadDisciplina.query.get(disc_id)
        professor = Servidor.query.get(prof_id)
        associacoes.append({'disciplina': disciplina, 'professor': professor})
    return render_template('academico/gerenciar_turma.html', turma=turma, disciplinas=disciplinas, professores=professores, associacoes=associacoes)

@academico_bp.route('/turmas/<int:turma_id>/desassociar/<int:disciplina_id>', methods=['POST'])
@role_required('admin', 'academico')
def desassociar_disciplina(turma_id, disciplina_id):
    try:
        stmt = db.delete(acad_turma_disciplinas_professores).where(db.and_(acad_turma_disciplinas_professores.c.turma_id == turma_id, acad_turma_disciplinas_professores.c.disciplina_id == disciplina_id))
        db.session.execute(stmt)
        db.session.commit()
        flash('Disciplina desassociada com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao desassociar disciplina: {e}', 'danger')
    return redirect(url_for('academico.gerenciar_turma_disciplinas', turma_id=turma_id))
	# (No final de academico_routes.py)



@academico_bp.route('/turmas/diario/<int:turma_id>', methods=['GET', 'POST'])
@role_required('admin', 'academico', 'RH')
def diario_de_classe(turma_id):
    turma = AcadTurma.query.get_or_404(turma_id)
    
    # --- LÓGICA DE FILTRO POR PERÍODO ---
    periodo_id_selecionado = request.args.get('periodo_id', type=int)

    # Busca os períodos e disciplinas disponíveis
    periodos_disponiveis = AcadPeriodo.query.filter_by(ano_letivo=turma.ano_letivo).order_by(AcadPeriodo.nome).all()
    
    stmt = db.select(acad_turma_disciplinas_professores.c.disciplina_id, acad_turma_disciplinas_professores.c.professor_num_contrato).where(acad_turma_disciplinas_professores.c.turma_id == turma_id)
    associacoes_raw = db.session.execute(stmt).all()
    disciplinas_turma = sorted([AcadDisciplina.query.get(disc_id) for disc_id, _ in associacoes_raw], key=lambda d: d.nome)
    professores_map = {disc_id: prof_id for disc_id, prof_id in associacoes_raw}
    
    if request.method == 'POST':
        try:
            periodo_id_form = request.form.get('periodo_id', type=int)
            # O formulário envia dados no formato 'nota-{{matricula.id}}-{{disciplina.id}}'
            for key, valor in request.form.items():
                if key.startswith('nota-') and valor:
                    _, matricula_id, disciplina_id = key.split('-')
                    valor_nota = float(valor.replace(',', '.'))
                    
                    nota_existente = AcadNota.query.filter_by(
                        matricula_id=matricula_id,
                        disciplina_id=disciplina_id,
                        periodo_id=periodo_id_form
                    ).first()
                    
                    if nota_existente:
                        nota_existente.valor = valor_nota
                    else:
                        nova_nota = AcadNota(
                            valor=valor_nota,
                            matricula_id=matricula_id,
                            disciplina_id=disciplina_id,
                            periodo_id=periodo_id_form,
                            professor_num_contrato=professores_map.get(int(disciplina_id))
                        )
                        db.session.add(nova_nota)
            
            db.session.commit()
            flash('Notas salvas com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar as notas: {e}', 'danger')
        
        # Redireciona de volta para a mesma visão filtrada
        return redirect(url_for('academico.diario_de_classe', turma_id=turma.id, periodo_id=periodo_id_form))

    # Prepara um dicionário com as notas existentes no formato { 'matricula_id-disciplina_id': valor }
    notas_dict = {}
    if periodo_id_selecionado:
        notas_query = AcadNota.query.filter(
            AcadNota.periodo_id == periodo_id_selecionado,
            AcadNota.matricula.has(turma_id=turma_id)
        ).all()
        for nota in notas_query:
            chave = f"{nota.matricula_id}-{nota.disciplina_id}"
            notas_dict[chave] = nota.valor

    return render_template('academico/diario_de_classe.html',
                           turma=turma,
                           periodos_disponiveis=periodos_disponiveis,
                           disciplinas_turma=disciplinas_turma,
                           periodo_id_selecionado=periodo_id_selecionado,
                           notas_dict=notas_dict)
    
    
    
@academico_bp.route('/painel_principal')
@role_required('admin', 'academico', 'RH', 'professor') # Adicionei 'professor' para exemplo
def painel_principal():
    total_alunos = AcadAluno.query.count()
    # Pega o papel da sessão (definido no 'acesso_autorizado') para exibir o menu correto
    papel_academico = session.get('papel_academico') 
    
    return render_template('academico/dashboard.html', 
                           total_alunos=total_alunos, 
                           papel_academico=papel_academico)

@academico_bp.route('/', methods=['GET', 'POST'])
@role_required('admin', 'academico', 'RH')
def dashboard_acesso():
    if request.method == 'POST':
        # 1. Pega os dados do formulário do modal
        papel_escolhido = request.form.get('papel')
        codigo_acesso = request.form.get('codigo_acesso')
        
        # 2. Lógica de Validação (Ajuste esta lógica conforme seu BD)
        # Exemplo: Validação pelo número do contrato (que é a matrícula)
        servidor = Servidor.query.filter_by(num_contrato=codigo_acesso).first()
        
        # 3. VERIFICAÇÃO DE ACESSO
        if not servidor:
            flash('Código de acesso inválido.', 'danger')
            return redirect(url_for('academico.dashboard_acesso'))

        # Supondo que você use o num_contrato como código e que o servidor seja da Secretaria certa
        # Aqui você adicionaria mais lógica, ex:
        # if papel_escolhido == 'diretor' and servidor.funcao != 'Diretor':
        #     flash('Função não compatível com o acesso.', 'danger')
        #     return redirect(url_for('academico.dashboard_acesso'))

        # 4. Concede o acesso e salva a permissão específica na sessão
        session['papel_academico'] = papel_escolhido  # Salva o papel (diretor, professor, etc.)
        session['servidor_academico_id'] = servidor.num_contrato # Salva a matrícula para usar nas rotas
        
        flash(f'Acesso concedido como {papel_escolhido.capitalize()}.', 'success')
        
        # 5. Redireciona para o painel principal (nova rota)
        return redirect(url_for('academico.painel_principal'))
    
    # Se for GET (Acesso inicial), sempre redireciona para a modal de acesso
    return redirect(url_for('academico.painel_principal')) # Redireciona para exibir o painel    