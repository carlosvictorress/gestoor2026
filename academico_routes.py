from flask import Blueprint, render_template, request, flash, redirect, url_for, session, make_response
from models import (
    AcadAluno, Escola, AcadTurma, AcadMatricula, 
    AcadDisciplina, AcadPeriodo, Servidor, 
    acad_turma_disciplinas_professores, AcadNota,
    AcadHorarioAula, AcadFrequenciaDiaria, AcadDiarioConteudo
)
from extensions import db
from utils import role_required, cabecalho_e_rodape_moderno, currency_filter_br
from datetime import datetime, date, timedelta
from sqlalchemy import func, or_, and_
import io

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.pagesizes import A4, landscape

academico_bp = Blueprint('academico', __name__, url_prefix='/academico')

_schema_academico_verificado = False

def executar_migracao_academico_segura():
    from sqlalchemy import text
    try:
        db.create_all()
    except Exception as e:
        print(f"Aviso db.create_all(): {e}")

    # Criação explícita de tabelas do módulo acadêmico para PostgreSQL / Supabase
    tabelas_sql = [
        """
        CREATE TABLE IF NOT EXISTS acad_aluno (
            id SERIAL PRIMARY KEY,
            nome_completo VARCHAR(200) NOT NULL,
            cpf VARCHAR(14) UNIQUE,
            data_nascimento DATE NOT NULL,
            sexo VARCHAR(20),
            cor_raca VARCHAR(30),
            necessidade_especial BOOLEAN DEFAULT FALSE,
            tipo_deficiencia VARCHAR(100),
            nome_mae VARCHAR(200),
            nome_pai VARCHAR(200),
            telefone_responsavel VARCHAR(20),
            status VARCHAR(20) DEFAULT 'Ativo',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS acad_turma (
            id SERIAL PRIMARY KEY,
            escola_id INTEGER NOT NULL,
            nome VARCHAR(100) NOT NULL,
            ano_letivo INTEGER NOT NULL,
            etapa_ensino VARCHAR(100) NOT NULL,
            turno VARCHAR(20) NOT NULL,
            vagas INTEGER DEFAULT 30
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS acad_matricula (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER NOT NULL,
            turma_id INTEGER NOT NULL,
            data_matricula DATE NOT NULL,
            status VARCHAR(50) NOT NULL DEFAULT 'Cursando'
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS acad_disciplina (
            id SERIAL PRIMARY KEY,
            nome VARCHAR(100) NOT NULL,
            codigo VARCHAR(20),
            area_conhecimento VARCHAR(100)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS acad_periodo (
            id SERIAL PRIMARY KEY,
            nome VARCHAR(50) NOT NULL,
            ano_letivo INTEGER NOT NULL,
            data_inicio DATE,
            data_fim DATE
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS acad_nota (
            id SERIAL PRIMARY KEY,
            matricula_id INTEGER NOT NULL,
            disciplina_id INTEGER NOT NULL,
            periodo_id INTEGER NOT NULL,
            nota_valor FLOAT,
            nota_recuperacao FLOAT,
            conceito VARCHAR(20),
            faltas_bimestre INTEGER DEFAULT 0
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS acad_horario_aula (
            id SERIAL PRIMARY KEY,
            turma_id INTEGER NOT NULL,
            disciplina_id INTEGER NOT NULL,
            professor_num_contrato VARCHAR(50),
            dia_semana VARCHAR(20) NOT NULL,
            ordem_aula INTEGER NOT NULL,
            horario_inicio VARCHAR(10),
            horario_fim VARCHAR(10)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS acad_frequencia_diaria (
            id SERIAL PRIMARY KEY,
            data_chamada DATE NOT NULL,
            matricula_id INTEGER NOT NULL,
            turma_id INTEGER NOT NULL,
            disciplina_id INTEGER,
            status_presenca VARCHAR(10) NOT NULL DEFAULT 'P',
            justificativa TEXT,
            usuario_registro VARCHAR(100)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS acad_diario_conteudo (
            id SERIAL PRIMARY KEY,
            data_aula DATE NOT NULL,
            turma_id INTEGER NOT NULL,
            disciplina_id INTEGER NOT NULL,
            professor_num_contrato VARCHAR(50),
            conteudo_ministrado TEXT NOT NULL,
            habilidades_bncc TEXT,
            tarefa_casa TEXT
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS acad_turma_disciplinas_professores (
            turma_id INTEGER NOT NULL,
            servidor_cpf VARCHAR(14) NOT NULL,
            PRIMARY KEY (turma_id, servidor_cpf)
        );
        """
    ]

    for sql in tabelas_sql:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(sql))
        except Exception as e:
            print(f"Aviso ao criar tabela acadêmica: {e}")

    alteracoes = [
        ("acad_aluno", "nome_social", "VARCHAR(200)"),
        ("acad_aluno", "nis_aluno", "VARCHAR(20)"),
        ("acad_aluno", "uf_nascimento", "VARCHAR(2)"),
        ("acad_aluno", "municipio_nascimento", "VARCHAR(100)"),
        ("acad_aluno", "rg", "VARCHAR(20)"),
        ("acad_aluno", "orgao_emissor_rg", "VARCHAR(20)"),
        ("acad_aluno", "uf_rg", "VARCHAR(2)"),
        ("acad_aluno", "certidao_matricula", "VARCHAR(35)"),
        ("acad_aluno", "certidao_termo", "VARCHAR(20)"),
        ("acad_aluno", "certidao_folha", "VARCHAR(20)"),
        ("acad_aluno", "certidao_livro", "VARCHAR(20)"),
        ("acad_aluno", "certidao_cartorio", "VARCHAR(150)"),
        ("acad_aluno", "cpf_responsavel", "VARCHAR(14)"),
        ("acad_aluno", "nis_responsavel", "VARCHAR(20)"),
        ("acad_aluno", "whatsapp_responsavel", "VARCHAR(20)"),
        ("acad_aluno", "grau_parentesco", "VARCHAR(50)"),
        ("acad_aluno", "bairro", "VARCHAR(100)"),
        ("acad_aluno", "cep", "VARCHAR(10)"),
        ("acad_aluno", "zona_residencia", "VARCHAR(20) DEFAULT 'Urbana'"),
        ("acad_aluno", "utiliza_transporte_publico", "BOOLEAN DEFAULT FALSE"),
        ("acad_aluno", "modal_transporte", "VARCHAR(50)"),
        ("acad_aluno", "cid_laudo", "VARCHAR(20)"),
        ("acad_aluno", "cuidador_dedicado", "BOOLEAN DEFAULT FALSE"),
        ("acad_aluno", "restricoes_alimentares", "TEXT"),
        
        ("acad_turma", "codigo_inep_turma", "VARCHAR(20)"),
        ("acad_turma", "tipo_atendimento", "VARCHAR(100) DEFAULT 'Escolarização'"),
        ("acad_turma", "vagas_pne", "INTEGER DEFAULT 5"),

        ("acad_matricula", "numero_matricula", "VARCHAR(50)"),
        ("acad_matricula", "data_encerramento", "DATE"),
        ("acad_matricula", "observacoes", "TEXT"),

        ("acad_nota", "nota_recuperacao", "FLOAT"),
        ("acad_nota", "conceito", "VARCHAR(20)"),
        ("acad_nota", "faltas_bimestre", "INTEGER DEFAULT 0")
    ]

    for tabela, col_nome, col_tipo in alteracoes:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS {col_nome} {col_tipo};"))
        except Exception:
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {col_nome} {col_tipo};"))
            except Exception:
                pass


@academico_bp.before_request
def verificar_schema():
    global _schema_academico_verificado
    if not _schema_academico_verificado:
        try:
            executar_migracao_academico_segura()
            _schema_academico_verificado = True
        except Exception as e:
            print(f"Erro ao verificar schema no before_request: {e}")


# ===================================================================
# 1. DASHBOARD EXECUTIVO SIGE - SECRETARIA MUNICIPAL DE EDUCAÇÃO
# ===================================================================
@academico_bp.route('/')
@role_required('admin', 'academico', 'RH')
def dashboard():
    ano_atual = datetime.now().year
    
    try:
        total_alunos = AcadAluno.query.filter_by(status='Ativo').count()
    except Exception:
        db.session.rollback()
        total_alunos = 0

    try:
        alunos_bolsa_familia = AcadAluno.query.filter(AcadAluno.nis_aluno.isnot(None), AcadAluno.nis_aluno != '').count()
    except Exception:
        db.session.rollback()
        alunos_bolsa_familia = 0

    try:
        alunos_aee = AcadAluno.query.filter_by(necessidade_especial=True).count()
    except Exception:
        db.session.rollback()
        alunos_aee = 0

    try:
        total_turmas = AcadTurma.query.filter_by(ano_letivo=ano_atual).count()
    except Exception:
        db.session.rollback()
        total_turmas = 0

    try:
        total_escolas = Escola.query.filter_by(status='Ativa').count()
    except Exception:
        db.session.rollback()
        total_escolas = 0

    # Cálculo da taxa de ocupação das vagas
    try:
        vagas_totais_query = db.session.query(func.sum(AcadTurma.vagas)).filter_by(ano_letivo=ano_atual).scalar() or 0
        matriculas_ativas_query = db.session.query(func.count(AcadMatricula.id)).join(AcadTurma).filter(
            AcadTurma.ano_letivo == ano_atual,
            AcadMatricula.status.in_(['Cursando', 'Aprovado', 'Aprovado pelo Conselho'])
        ).scalar() or 0
        taxa_ocupacao = round((matriculas_ativas_query / vagas_totais_query * 100), 1) if vagas_totais_query > 0 else 0
    except Exception:
        db.session.rollback()
        taxa_ocupacao = 0

    # Gráfico de Alunos por Etapa de Ensino
    try:
        etapas_query = db.session.query(
            AcadTurma.etapa_ensino,
            func.count(AcadMatricula.id).label('total')
        ).join(AcadMatricula).filter(
            AcadTurma.ano_letivo == ano_atual,
            AcadMatricula.status == 'Cursando'
        ).group_by(AcadTurma.etapa_ensino).all()
        etapas_labels = [e.etapa_ensino for e in etapas_query] if etapas_query else ['Sem matrículas']
        etapas_data = [int(e.total) for e in etapas_query] if etapas_query else [0]
    except Exception:
        db.session.rollback()
        etapas_labels = ['Sem dados']
        etapas_data = [0]

    # Gráfico de Matrículas por Escola
    try:
        escolas_query = db.session.query(
            Escola.nome,
            func.count(AcadMatricula.id).label('total')
        ).join(AcadTurma, AcadTurma.escola_id == Escola.id)\
         .join(AcadMatricula, AcadMatricula.turma_id == AcadTurma.id)\
         .filter(AcadTurma.ano_letivo == ano_atual, AcadMatricula.status == 'Cursando')\
         .group_by(Escola.id, Escola.nome).limit(6).all()
        escolas_labels = [e.nome[:22] for e in escolas_query] if escolas_query else ['Sem dados']
        escolas_data = [int(e.total) for e in escolas_query] if escolas_query else [0]
    except Exception:
        db.session.rollback()
        escolas_labels = ['Sem dados']
        escolas_data = [0]

    # Alerta de Evasão Escolar (Falta acentuada > 15%)
    try:
        alunos_alerta_evasao = db.session.query(
            AcadAluno.nome_completo,
            Escola.nome.label('escola_nome'),
            AcadTurma.nome.label('turma_nome'),
            func.count(AcadFrequenciaDiaria.id).label('total_faltas')
        ).join(AcadMatricula, AcadMatricula.aluno_id == AcadAluno.id)\
         .join(AcadTurma, AcadMatricula.turma_id == AcadTurma.id)\
         .join(Escola, AcadTurma.escola_id == Escola.id)\
         .join(AcadFrequenciaDiaria, AcadFrequenciaDiaria.matricula_id == AcadMatricula.id)\
         .filter(AcadFrequenciaDiaria.status_presenca == 'F')\
         .group_by(AcadAluno.id, AcadAluno.nome_completo, Escola.nome, AcadTurma.nome)\
         .having(func.count(AcadFrequenciaDiaria.id) >= 5)\
         .order_by(func.count(AcadFrequenciaDiaria.id).desc()).limit(10).all()
    except Exception as e:
        db.session.rollback()
        print(f"Aviso ao carregar alerta de evasão: {e}")
        alunos_alerta_evasao = []

    return render_template(
        'academico/dashboard.html',
        total_alunos=total_alunos,
        alunos_bolsa_familia=alunos_bolsa_familia,
        alunos_aee=alunos_aee,
        total_turmas=total_turmas,
        total_escolas=total_escolas,
        taxa_ocupacao=taxa_ocupacao,
        etapas_labels=etapas_labels,
        etapas_data=etapas_data,
        escolas_labels=escolas_labels,
        escolas_data=escolas_data,
        alunos_alerta_evasao=alunos_alerta_evasao,
        ano_atual=ano_atual
    )


# ===================================================================
# 2. PRONTUÁRIO DO ALUNO & GESTÃO COMPLETA (EDUCACENSO / INEP)
# ===================================================================
@academico_bp.route('/alunos', methods=['GET', 'POST'])
@role_required('admin', 'academico', 'RH')
def gerenciar_alunos():
    if request.method == 'POST':
        try:
            data_nasc_str = request.form.get('data_nascimento')
            data_nasc = datetime.strptime(data_nasc_str, '%Y-%m-%d').date() if data_nasc_str else None

            novo_aluno = AcadAluno(
                nome_completo=request.form.get('nome_completo'),
                nome_social=request.form.get('nome_social'),
                data_nascimento=data_nasc,
                sexo=request.form.get('sexo'),
                cor_raca=request.form.get('cor_raca'),
                filiacao_1=request.form.get('filiacao_1'),
                filiacao_2=request.form.get('filiacao_2'),
                nacionalidade=request.form.get('nacionalidade', 'Brasileira'),
                uf_nascimento=request.form.get('uf_nascimento'),
                municipio_nascimento=request.form.get('municipio_nascimento'),
                cpf=request.form.get('cpf'),
                rg=request.form.get('rg'),
                orgao_emissor_rg=request.form.get('orgao_emissor_rg'),
                uf_rg=request.form.get('uf_rg'),
                id_inep=request.form.get('id_inep'),
                nis_aluno=request.form.get('nis_aluno'),
                certidao_matricula=request.form.get('certidao_matricula'),
                certidao_termo=request.form.get('certidao_termo'),
                certidao_folha=request.form.get('certidao_folha'),
                certidao_livro=request.form.get('certidao_livro'),
                certidao_cartorio=request.form.get('certidao_cartorio'),
                nome_responsavel=request.form.get('nome_responsavel'),
                cpf_responsavel=request.form.get('cpf_responsavel'),
                nis_responsavel=request.form.get('nis_responsavel'),
                telefone_responsavel=request.form.get('telefone_responsavel'),
                whatsapp_responsavel=request.form.get('whatsapp_responsavel'),
                grau_parentesco=request.form.get('grau_parentesco'),
                endereco=request.form.get('endereco'),
                bairro=request.form.get('bairro'),
                cep=request.form.get('cep'),
                zona_residencia=request.form.get('zona_residencia', 'Urbana'),
                utiliza_transporte_publico='utiliza_transporte_publico' in request.form,
                modal_transporte=request.form.get('modal_transporte'),
                necessidade_especial='necessidade_especial' in request.form,
                tipo_necessidade=request.form.get('tipo_necessidade'),
                cid_laudo=request.form.get('cid_laudo'),
                cuidador_dedicado='cuidador_dedicado' in request.form,
                restricoes_alimentares=request.form.get('restricoes_alimentares')
            )
            db.session.add(novo_aluno)
            db.session.commit()
            flash(f'Aluno "{novo_aluno.nome_completo}" cadastrado com sucesso no Prontuário Educacenso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar aluno: {e}', 'danger')
        return redirect(url_for('academico.gerenciar_alunos'))

    busca = request.args.get('busca')
    query = AcadAluno.query.order_by(AcadAluno.nome_completo.asc())
    if busca:
        query = query.filter(
            or_(
                AcadAluno.nome_completo.ilike(f'%{busca}%'),
                AcadAluno.cpf.like(f'%{busca}%'),
                AcadAluno.id_inep.like(f'%{busca}%'),
                AcadAluno.nis_aluno.like(f'%{busca}%')
            )
        )
    alunos = query.all()
    return render_template('academico/alunos_lista.html', alunos=alunos, busca=busca)


@academico_bp.route('/alunos/editar/<int:aluno_id>', methods=['POST'])
@role_required('admin', 'academico', 'RH')
def editar_aluno(aluno_id):
    aluno = AcadAluno.query.get_or_404(aluno_id)
    try:
        data_nasc_str = request.form.get('data_nascimento')
        aluno.nome_completo = request.form.get('nome_completo')
        aluno.nome_social = request.form.get('nome_social')
        aluno.data_nascimento = datetime.strptime(data_nasc_str, '%Y-%m-%d').date() if data_nasc_str else aluno.data_nascimento
        aluno.sexo = request.form.get('sexo')
        aluno.cor_raca = request.form.get('cor_raca')
        aluno.filiacao_1 = request.form.get('filiacao_1')
        aluno.filiacao_2 = request.form.get('filiacao_2')
        aluno.cpf = request.form.get('cpf')
        aluno.rg = request.form.get('rg')
        aluno.orgao_emissor_rg = request.form.get('orgao_emissor_rg')
        aluno.uf_rg = request.form.get('uf_rg')
        aluno.id_inep = request.form.get('id_inep')
        aluno.nis_aluno = request.form.get('nis_aluno')
        aluno.certidao_matricula = request.form.get('certidao_matricula')
        aluno.nome_responsavel = request.form.get('nome_responsavel')
        aluno.cpf_responsavel = request.form.get('cpf_responsavel')
        aluno.nis_responsavel = request.form.get('nis_responsavel')
        aluno.telefone_responsavel = request.form.get('telefone_responsavel')
        aluno.whatsapp_responsavel = request.form.get('whatsapp_responsavel')
        aluno.endereco = request.form.get('endereco')
        aluno.bairro = request.form.get('bairro')
        aluno.zona_residencia = request.form.get('zona_residencia', 'Urbana')
        aluno.utiliza_transporte_publico = 'utiliza_transporte_publico' in request.form
        aluno.modal_transporte = request.form.get('modal_transporte')
        aluno.necessidade_especial = 'necessidade_especial' in request.form
        aluno.tipo_necessidade = request.form.get('tipo_necessidade')
        aluno.cid_laudo = request.form.get('cid_laudo')
        aluno.cuidador_dedicado = 'cuidador_dedicado' in request.form
        aluno.restricoes_alimentares = request.form.get('restricoes_alimentares')

        db.session.commit()
        flash('Prontuário do aluno atualizado com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao atualizar aluno: {e}', 'danger')
    return redirect(url_for('academico.gerenciar_alunos'))


@academico_bp.route('/alunos/ficha/<int:aluno_id>', methods=['GET'])
@role_required('admin', 'academico', 'RH')
def ficha_aluno(aluno_id):
    aluno = AcadAluno.query.get_or_404(aluno_id)
    gerar_pdf = request.args.get('gerar_pdf')

    if gerar_pdf:
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import cm

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=3*cm, bottomMargin=2*cm)
        styles = getSampleStyleSheet()

        story = []
        story.append(Paragraph(f"FICHA CADASTRAL INDIVIDUAL DO ALUNO — INEP #{aluno.id_inep or 'PENDENTE'}", styles['h2']))
        story.append(Paragraph(f"Emissão em {datetime.now().strftime('%d/%m/%Y %H:%M')} | Sistema SIGE Municipal", styles['Normal']))
        story.append(Spacer(1, 0.4*cm))

        data_aluno = [
            ['Nome Completo:', aluno.nome_completo, 'Data Nasc:', aluno.data_nascimento.strftime('%d/%m/%Y')],
            ['CPF Aluno:', aluno.cpf or 'Não Informado', 'NIS / PIS:', aluno.nis_aluno or 'Não Informado'],
            ['Mãe / Filiação 1:', aluno.filiacao_1 or '--', 'Pai / Filiação 2:', aluno.filiacao_2 or '--'],
            ['Responsável Legal:', aluno.nome_responsavel or '--', 'Contato:', aluno.telefone_responsavel or '--'],
            ['Endereço:', aluno.endereco or '--', 'Zona:', aluno.zona_residencia or 'Urbana'],
            ['Necessidade Especial:', 'SIM' if aluno.necessidade_especial else 'NÃO', 'Laudo / CID:', aluno.cid_laudo or 'N/A']
        ]

        t = Table(data_aluno, colWidths=[4*cm, 6.5*cm, 3.5*cm, 4*cm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f0f4f8')),
            ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#f0f4f8')),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 0), (-1, -1), 9)
        ]))
        story.append(t)
        story.append(Spacer(1, 1.5*cm))

        story.append(Paragraph("________________________________________", styles['Normal']))
        story.append(Paragraph("Assinatura do Responsável / Secretaria Escolar", styles['Normal']))

        doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Ficha Cadastral do Aluno"),
                         onLaterPages=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Ficha Cadastral do Aluno"))
        buffer.seek(0)
        resp = make_response(buffer.getvalue())
        resp.headers['Content-Type'] = 'application/pdf'
        resp.headers['Content-Disposition'] = f'inline; filename=ficha_aluno_{aluno.id}.pdf'
        return resp

    return render_template('academico/aluno_ficha.html', aluno=aluno)


# ===================================================================
# 3. GESTÃO DE TURMAS, VAGAS E GRADE HORÁRIA SEMANAL
# ===================================================================
@academico_bp.route('/turmas', methods=['GET'])
@role_required('admin', 'academico', 'RH')
def gerenciar_turmas():
    ano_selecionado = request.args.get('ano', datetime.now().year, type=int)
    escola_id = request.args.get('escola_id', type=int)
    
    query = AcadTurma.query.filter_by(ano_letivo=ano_selecionado)
    if escola_id:
        query = query.filter_by(escola_id=escola_id)
        
    turmas = query.order_by(AcadTurma.escola_id, AcadTurma.nome).all()
    escolas = Escola.query.order_by(Escola.nome).all()
    return render_template('academico/turmas_lista.html', turmas=turmas, escolas=escolas, ano_selecionado=ano_selecionado, escola_selecionada_id=escola_id)


@academico_bp.route('/turmas/nova', methods=['POST'])
@role_required('admin', 'academico', 'RH')
def nova_turma():
    try:
        nova = AcadTurma(
            nome=request.form.get('nome'),
            codigo_inep_turma=request.form.get('codigo_inep_turma'),
            ano_letivo=request.form.get('ano_letivo', type=int),
            turno=request.form.get('turno'),
            etapa_ensino=request.form.get('etapa_ensino'),
            modalidade=request.form.get('modalidade', 'Regular'),
            tipo_atendimento=request.form.get('tipo_atendimento', 'Escolarização'),
            vagas=request.form.get('vagas', 30, type=int),
            vagas_pne=request.form.get('vagas_pne', 5, type=int),
            escola_id=request.form.get('escola_id', type=int)
        )
        db.session.add(nova)
        db.session.commit()
        flash('Turma criada com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao criar turma: {e}', 'danger')
    return redirect(url_for('academico.gerenciar_turmas', ano=request.form.get('ano_letivo')))


@academico_bp.route('/turmas/detalhes/<int:turma_id>')
@role_required('admin', 'academico', 'RH')
def detalhes_turma(turma_id):
    turma = AcadTurma.query.get_or_404(turma_id)
    alunos_para_matricular = AcadAluno.query.filter(
        AcadAluno.status == 'Ativo',
        ~AcadAluno.matriculas.any(and_(AcadMatricula.turma_id == turma_id, AcadMatricula.status == 'Cursando'))
    ).order_by(AcadAluno.nome_completo).all()

    disciplinas = AcadDisciplina.query.order_by(AcadDisciplina.nome).all()
    professores = Servidor.query.filter(or_(Servidor.funcao.ilike('%profess%'), Servidor.funcao.ilike('%docent%'), Servidor.funcao.ilike('%educador%'))).order_by(Servidor.nome).all()
    if not professores:
        professores = Servidor.query.order_by(Servidor.nome).all()
    horarios = AcadHorarioAula.query.filter_by(turma_id=turma_id).order_by(AcadHorarioAula.ordem_aula).all()

    return render_template(
        'academico/turma_detalhes.html',
        turma=turma,
        alunos_para_matricular=alunos_para_matricular,
        disciplinas=disciplinas,
        professores=professores,
        horarios=horarios
    )


# ===================================================================
# 4. MATRÍCULA RÁPIDA, TRANSFERÊNCIAS E DOCUMENTOS TIMBRADOS
# ===================================================================
@academico_bp.route('/turmas/<int:turma_id>/matricular', methods=['POST'])
@role_required('admin', 'academico', 'RH')
def matricular_aluno(turma_id):
    turma = AcadTurma.query.get_or_404(turma_id)
    aluno_id = request.form.get('aluno_id', type=int)
    
    if not aluno_id:
        flash('Nenhum aluno selecionado.', 'danger')
        return redirect(url_for('academico.detalhes_turma', turma_id=turma_id))
        
    matriculas_ativas = [m for m in turma.matriculas if m.status == 'Cursando']
    if len(matriculas_ativas) >= turma.vagas:
        flash('Atenção: Limite máximo de vagas atingido para esta turma!', 'warning')

    try:
        # Gera código único de matrícula (Ex: 2026-ESC01-0042)
        seq = db.session.query(func.count(AcadMatricula.id)).scalar() + 1
        num_mat = f"{turma.ano_letivo}-ESC{turma.escola_id:02d}-{seq:04d}"

        nova = AcadMatricula(
            numero_matricula=num_mat,
            aluno_id=aluno_id,
            turma_id=turma_id,
            data_matricula=datetime.now().date(),
            status='Cursando'
        )
        db.session.add(nova)
        db.session.commit()
        flash(f'Matrícula Nº {num_mat} efetuada com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao matricular aluno: {e}', 'danger')

    return redirect(url_for('academico.detalhes_turma', turma_id=turma_id))


@academico_bp.route('/matricula/comprovante/<int:matricula_id>')
@role_required('admin', 'academico', 'RH')
def comprovante_matricula_pdf(matricula_id):
    mat = AcadMatricula.query.get_or_404(matricula_id)
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=3*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()

    story = []
    story.append(Paragraph("COMPROVANTE OFICIAL DE MATRÍCULA ESCOLAR", styles['h2']))
    story.append(Paragraph(f"Ano Letivo {mat.turma.ano_letivo} | Matrícula Nº: {mat.numero_matricula or mat.id}", styles['Normal']))
    story.append(Spacer(1, 0.5*cm))

    dados = [
        ['Nome do Aluno:', mat.aluno.nome_completo, 'CPF / INEP:', f"{mat.aluno.cpf or '--'} / {mat.aluno.id_inep or '--'}"],
        ['Unidade Escolar:', mat.turma.escola.nome, 'Turma / Turno:', f"{mat.turma.nome} ({mat.turma.turno})"],
        ['Etapa de Ensino:', mat.turma.etapa_ensino, 'Data da Matrícula:', mat.data_matricula.strftime('%d/%m/%Y')],
        ['Responsável Legal:', mat.aluno.nome_responsavel or '--', 'Contato:', mat.aluno.telefone_responsavel or '--']
    ]

    t = Table(dados, colWidths=[4*cm, 6.5*cm, 3.5*cm, 4*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#e8f5e9')),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#e8f5e9')),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTSIZE', (0, 0), (-1, -1), 9)
    ]))
    story.append(t)
    story.append(Spacer(1, 2*cm))

    story.append(Paragraph("________________________________________", styles['Normal']))
    story.append(Paragraph("Secretaria Escolar / Gestão de Rede Municipal", styles['Normal']))

    doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Comprovante de Matrícula"),
                     onLaterPages=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Comprovante de Matrícula"))
    buffer.seek(0)
    resp = make_response(buffer.getvalue())
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'inline; filename=comprovante_matricula_{mat.id}.pdf'
    return resp


# ===================================================================
# 5. DIÁRIO DE CLASSE DIGITAL (CHAMADA DIÁRIA & CONTEÚDO BNCC)
# ===================================================================
@academico_bp.route('/diario', methods=['GET', 'POST'])
@role_required('admin', 'academico', 'RH')
def diario_classe():
    turma_id = request.args.get('turma_id', type=int) or request.form.get('turma_id', type=int)
    data_sel_str = request.args.get('data', datetime.now().strftime('%Y-%m-%d'))
    data_sel = datetime.strptime(data_sel_str, '%Y-%m-%d').date()

    turmas = AcadTurma.query.filter_by(ano_letivo=datetime.now().year).all()
    turma = AcadTurma.query.get(turma_id) if turma_id else (turmas[0] if turmas else None)

    if request.method == 'POST' and turma:
        try:
            # Registrar Frequência dos Alunos
            for mat in turma.matriculas:
                status = request.form.get(f'presenca_{mat.id}', 'P')
                just = request.form.get(f'justificativa_{mat.id}', '')
                
                freq = AcadFrequenciaDiaria.query.filter_by(
                    data_chamada=data_sel,
                    matricula_id=mat.id
                ).first()

                if not freq:
                    freq = AcadFrequenciaDiaria(
                        data_chamada=data_sel,
                        matricula_id=mat.id,
                        turma_id=turma.id,
                        status_presenca=status,
                        justificativa=just,
                        usuario_registro=session.get('username', 'Professor')
                    )
                    db.session.add(freq)
                else:
                    freq.status_presenca = status
                    freq.justificativa = just

            # Registrar Conteúdo da Aula (BNCC)
            conteudo_txt = request.form.get('conteudo_ministrado')
            if conteudo_txt:
                disc_id = request.form.get('disciplina_id', type=int)
                if disc_id:
                    diario = AcadDiarioConteudo.query.filter_by(data_aula=data_sel, turma_id=turma.id, disciplina_id=disc_id).first()
                    if not diario:
                        diario = AcadDiarioConteudo(
                            data_aula=data_sel,
                            turma_id=turma.id,
                            disciplina_id=disc_id,
                            conteudo_ministrado=conteudo_txt,
                            habilidades_bncc=request.form.get('habilidades_bncc'),
                            tarefa_casa=request.form.get('tarefa_casa')
                        )
                        db.session.add(diario)
                    else:
                        diario.conteudo_ministrado = conteudo_txt

            db.session.commit()
            flash(f'Diário de Classe registrado para o dia {data_sel.strftime("%d/%m/%Y")}!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar Diário de Classe: {e}', 'danger')

    disciplinas = AcadDisciplina.query.all()
    frequencias_existentes = {}
    if turma:
        records = AcadFrequenciaDiaria.query.filter_by(turma_id=turma.id, data_chamada=data_sel).all()
        frequencias_existentes = {r.matricula_id: r for r in records}

    return render_template(
        'academico/diario_classe.html',
        turmas=turmas,
        turma_selecionada=turma,
        data_selecionada=data_sel,
        disciplinas=disciplinas,
        frequencias_existentes=frequencias_existentes
    )


# ===================================================================
# 6. LANÇAMENTO DE NOTAS, CONCEITOS E FECHAMENTO ANUAL
# ===================================================================
@academico_bp.route('/notas', methods=['GET', 'POST'])
@role_required('admin', 'academico', 'RH')
def lancar_notas():
    turma_id = request.args.get('turma_id', type=int) or request.form.get('turma_id', type=int)
    periodo_id = request.args.get('periodo_id', type=int) or request.form.get('periodo_id', type=int)
    disciplina_id = request.args.get('disciplina_id', type=int) or request.form.get('disciplina_id', type=int)

    turmas = AcadTurma.query.filter_by(ano_letivo=datetime.now().year).all()
    periodos = AcadPeriodo.query.order_by(AcadPeriodo.nome).all()
    disciplinas = AcadDisciplina.query.order_by(AcadDisciplina.nome).all()

    turma = AcadTurma.query.get(turma_id) if turma_id else None
    periodo = AcadPeriodo.query.get(periodo_id) if periodo_id else None
    disciplina = AcadDisciplina.query.get(disciplina_id) if disciplina_id else None

    if request.method == 'POST' and turma and periodo and disciplina:
        try:
            for mat in turma.matriculas:
                val_str = request.form.get(f'nota_{mat.id}', '').replace(',', '.')
                conc = request.form.get(f'conceito_{mat.id}')
                rec_str = request.form.get(f'rec_{mat.id}', '').replace(',', '.')
                faltas_str = request.form.get(f'faltas_{mat.id}', '0')

                val = float(val_str) if val_str != '' else None
                rec = float(rec_str) if rec_str != '' else None
                faltas = int(faltas_str) if faltas_str != '' else 0

                nota = AcadNota.query.filter_by(
                    matricula_id=mat.id,
                    disciplina_id=disciplina.id,
                    periodo_id=periodo.id
                ).first()

                if not nota:
                    nota = AcadNota(
                        matricula_id=mat.id,
                        disciplina_id=disciplina.id,
                        periodo_id=periodo.id,
                        valor=val,
                        nota_recuperacao=rec,
                        conceito=conc,
                        faltas_bimestre=faltas
                    )
                    db.session.add(nota)
                else:
                    nota.valor = val
                    nota.nota_recuperacao = rec
                    nota.conceito = conc
                    nota.faltas_bimestre = faltas

            db.session.commit()
            flash('Notas e faltas do período lançadas com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar notas: {e}', 'danger')

    notas_existentes = {}
    if turma and periodo and disciplina:
        recs = AcadNota.query.filter_by(disciplina_id=disciplina.id, periodo_id=periodo.id).all()
        notas_existentes = {r.matricula_id: r for r in recs}

    return render_template(
        'academico/notas_lancamento.html',
        turmas=turmas,
        periodos=periodos,
        disciplinas=disciplinas,
        turma_sel=turma,
        periodo_sel=periodo,
        disciplina_sel=disciplina,
        notas_existentes=notas_existentes
    )


# ===================================================================
# 7. CENTRAL DE DOCUMENTOS TIMBRADOS (BOLETIM E ATA FINAL)
# ===================================================================
@academico_bp.route('/documentos')
@role_required('admin', 'academico', 'RH')
def central_documentos():
    turmas = AcadTurma.query.order_by(AcadTurma.ano_letivo.desc(), AcadTurma.nome).all()
    alunos = AcadAluno.query.order_by(AcadAluno.nome_completo).all()
    return render_template('academico/documentos.html', turmas=turmas, alunos=alunos)


@academico_bp.route('/documentos/boletim/<int:matricula_id>')
@role_required('admin', 'academico', 'RH')
def gerar_boletim_pdf(matricula_id):
    mat = AcadMatricula.query.get_or_404(matricula_id)
    disciplinas = AcadDisciplina.query.order_by(AcadDisciplina.nome).all()
    periodos = AcadPeriodo.query.order_by(AcadPeriodo.nome).all()

    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=3*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()

    story = []
    story.append(Paragraph("BOLETIM ESCOLAR OFICIAL DE RENDIMENTO", styles['h2']))
    story.append(Paragraph(f"Aluno(a): {mat.aluno.nome_completo} | Turma: {mat.turma.nome} ({mat.turma.ano_letivo})", styles['Normal']))
    story.append(Paragraph(f"Escola: {mat.turma.escola.nome}", styles['Normal']))
    story.append(Spacer(1, 0.5*cm))

    # Matriz de Notas
    header = ['Disciplina'] + [p.nome for p in periodos] + ['Média Final', 'Faltas']
    table_data = [header]

    for disc in disciplinas:
        row = [disc.nome]
        soma_notas = 0.0
        qtd_notas = 0
        total_faltas_disc = 0

        for p in periodos:
            nota_obj = AcadNota.query.filter_by(matricula_id=mat.id, disciplina_id=disc.id, periodo_id=p.id).first()
            if nota_obj:
                val = nota_obj.nota_recuperacao if (nota_obj.nota_recuperacao and nota_obj.nota_recuperacao > (nota_obj.valor or 0)) else (nota_obj.valor or 0.0)
                row.append(f"{val:.1f}")
                soma_notas += val
                qtd_notas += 1
                total_faltas_disc += nota_obj.faltas_bimestre or 0
            else:
                row.append("--")

        media_final = (soma_notas / qtd_notas) if qtd_notas > 0 else 0.0
        row.append(f"{media_final:.1f}")
        row.append(str(total_faltas_disc))
        table_data.append(row)

    t = Table(table_data, colWidths=[6*cm] + [2.2*cm]*len(periodos) + [2.5*cm, 2*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d47a1')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')])
    ]))

    story.append(t)
    story.append(Spacer(1, 1.5*cm))
    story.append(Paragraph("________________________________________", styles['Normal']))
    story.append(Paragraph("Secretario Escolar / Diretor Pedagógico", styles['Normal']))

    doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Boletim Escolar Oficial"),
                     onLaterPages=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Boletim Escolar Oficial"))
    buffer.seek(0)
    resp = make_response(buffer.getvalue())
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'inline; filename=boletim_{mat.id}.pdf'
    return resp


@academico_bp.route('/documentos/ata-final/<int:turma_id>')
@role_required('admin', 'academico', 'RH')
def gerar_ata_final_pdf(turma_id):
    turma = AcadTurma.query.get_or_404(turma_id)
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=3*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()

    story = []
    story.append(Paragraph(f"ATA FINAL DE RENDIMENTO ESCOLAR — TURMA {turma.nome}", styles['h2']))
    story.append(Paragraph(f"Escola: {turma.escola.nome} | Ano Letivo: {turma.ano_letivo}", styles['Normal']))
    story.append(Spacer(1, 0.5*cm))

    table_data = [['Nº', 'Nome do Aluno', 'Matrícula', 'Situação Final do Aluno']]
    idx = 1
    for m in turma.matriculas:
        table_data.append([
            str(idx),
            m.aluno.nome_completo,
            m.numero_matricula or f"MAT-{m.id}",
            m.status
        ])
        idx += 1

    t = Table(table_data, colWidths=[1.5*cm, 9*cm, 4*cm, 3.5*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#004d40')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 1), (1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')])
    ]))

    story.append(t)
    story.append(Spacer(1, 1.5*cm))
    story.append(Paragraph("________________________________________", styles['Normal']))
    story.append(Paragraph("Comissão de Fechamento de Ano / Conselho de Classe", styles['Normal']))

    doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Ata Final de Rendimento Escolar"),
                     onLaterPages=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Ata Final de Rendimento Escolar"))
    buffer.seek(0)
    resp = make_response(buffer.getvalue())
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'inline; filename=ata_final_turma_{turma.id}.pdf'
    return resp


# ===================================================================
# 8. AUDITORIA & VALIDADOR DO CENSO ESCOLAR (EDUCACENSO / INEP)
# ===================================================================
@academico_bp.route('/educacenso')
@role_required('admin', 'academico', 'RH')
def auditoria_educacenso():
    pendencias_alunos = []
    
    alunos = AcadAluno.query.filter_by(status='Ativo').all()
    for a in alunos:
        erros = []
        if not a.cpf and not a.certidao_matricula:
            erros.append('Falta CPF ou Certidão de Nascimento 32 dígitos')
        if not a.id_inep:
            erros.append('Código INEP do Aluno ausente')
        if not a.nis_aluno:
            erros.append('NIS / PIS não informado (Bolsa Família)')
        if not a.data_nascimento:
            erros.append('Data de nascimento ausente')

        if erros:
            pendencias_alunos.append({
                'aluno': a,
                'erros': erros
            })

    total_alunos_ok = len(alunos) - len(pendencias_alunos)
    taxa_conformidade = round((total_alunos_ok / len(alunos) * 100), 1) if len(alunos) > 0 else 100

    return render_template(
        'academico/educacenso.html',
        pendencias_alunos=pendencias_alunos,
        total_alunos=len(alunos),
        total_alunos_ok=total_alunos_ok,
        taxa_conformidade=taxa_conformidade
    )


# ===================================================================
# 9. GESTÃO DE DISCIPLINAS & MATRIZ CURRICULAR
# ===================================================================
@academico_bp.route('/disciplinas', methods=['GET', 'POST'])
@role_required('admin', 'academico', 'RH')
def gerenciar_disciplinas():
    if request.method == 'POST':
        try:
            disc_id = request.form.get('id', type=int)
            nome = request.form.get('nome')
            codigo = request.form.get('codigo')
            area = request.form.get('area_conhecimento', 'Outros')
            
            if disc_id:
                disc = AcadDisciplina.query.get_or_404(disc_id)
                disc.nome = nome
                disc.codigo = codigo
                disc.area_conhecimento = area
                flash(f'Disciplina "{nome}" atualizada com sucesso!', 'success')
            else:
                nova_disc = AcadDisciplina(
                    nome=nome,
                    codigo=codigo,
                    area_conhecimento=area
                )
                db.session.add(nova_disc)
                flash(f'Disciplina "{nome}" cadastrada com sucesso!', 'success')
            
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar disciplina: {e}', 'danger')
        return redirect(url_for('academico.gerenciar_disciplinas'))

    disciplinas = AcadDisciplina.query.order_by(AcadDisciplina.area_conhecimento, AcadDisciplina.nome).all()
    areas = ['Linguagens', 'Matemática', 'Ciências da Natureza', 'Ciências Humanas', 'Ensino Religioso', 'Diversificada', 'Outros']
    return render_template('academico/disciplinas.html', disciplinas=disciplinas, areas=areas)


@academico_bp.route('/disciplinas/excluir/<int:id>')
@role_required('admin', 'academico')
def excluir_disciplina(id):
    disc = AcadDisciplina.query.get_or_404(id)
    try:
        db.session.delete(disc)
        db.session.commit()
        flash(f'Disciplina "{disc.nome}" excluída.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Não foi possível excluir a disciplina: {e}', 'danger')
    return redirect(url_for('academico.gerenciar_disciplinas'))


# ===================================================================
# 10. GESTÃO DE PROFESSORES & ATRIBUIÇÕES DE AULA
# ===================================================================
@academico_bp.route('/professores', methods=['GET', 'POST'])
@role_required('admin', 'academico', 'RH')
def gerenciar_professores():
    ano_atual = datetime.now().year
    
    if request.method == 'POST':
        try:
            turma_id = request.form.get('turma_id', type=int)
            servidor_cpf = request.form.get('servidor_cpf')
            
            turma = AcadTurma.query.get_or_404(turma_id)
            servidor = Servidor.query.filter_by(cpf=servidor_cpf).first()
            
            if servidor and servidor not in turma.disciplinas_professores:
                turma.disciplinas_professores.append(servidor)
                db.session.commit()
                flash(f'Professor(a) {servidor.nome} vinculado(a) à turma {turma.nome}!', 'success')
            else:
                flash('Professor(a) já está vinculado(a) a esta turma ou servidor não encontrado.', 'warning')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atribuir professor: {e}', 'danger')
        return redirect(url_for('academico.gerenciar_professores'))

    professores = Servidor.query.filter(or_(Servidor.funcao.ilike('%profess%'), Servidor.funcao.ilike('%docent%'), Servidor.funcao.ilike('%educador%'))).order_by(Servidor.nome).all()
    if not professores:
        professores = Servidor.query.order_by(Servidor.nome).all()
        
    turmas = AcadTurma.query.filter_by(ano_letivo=ano_atual).order_by(AcadTurma.nome).all()
    disciplinas = AcadDisciplina.query.order_by(AcadDisciplina.nome).all()

    return render_template(
        'academico/professores.html',
        professores=professores,
        turmas=turmas,
        disciplinas=disciplinas
    )


# ===================================================================
# 11. GERADOR EXECUTIVO DE RELATÓRIOS EM PDF (SIGE 360)
# ===================================================================

@academico_bp.route('/boletim/<int:matricula_id>/pdf')
@role_required('admin', 'academico', 'RH')
def pdf_boletim_escolar(matricula_id):
    matricula = AcadMatricula.query.get_or_404(matricula_id)
    aluno = matricula.aluno
    turma = matricula.turma
    escola = turma.escola

    periodos = AcadPeriodo.query.filter_by(ano_letivo=turma.ano_letivo).order_by(AcadPeriodo.id).all()
    if not periodos:
        periodos = [AcadPeriodo(id=1, nome="1º Bimestre"), AcadPeriodo(id=2, nome="2º Bimestre"), AcadPeriodo(id=3, nome="3º Bimestre"), AcadPeriodo(id=4, nome="4º Bimestre")]

    disciplinas = AcadDisciplina.query.order_by(AcadDisciplina.nome).all()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=2.8*cm, bottomMargin=1.5*cm)
    
    styles = getSampleStyleSheet()
    style_titulo = ParagraphStyle('Titulo', parent=styles['Heading1'], alignment=TA_CENTER, fontSize=12, leading=14, fontName='Helvetica-Bold', textColor=colors.HexColor('#0f5132'))
    style_sub = ParagraphStyle('Sub', parent=styles['Normal'], alignment=TA_CENTER, fontSize=9, leading=11, fontName='Helvetica-Oblique', textColor=colors.HexColor('#444444'))
    style_label = ParagraphStyle('Label', parent=styles['Normal'], fontSize=8, leading=10, fontName='Helvetica-Bold', textColor=colors.HexColor('#1b4332'))
    style_val = ParagraphStyle('Val', parent=styles['Normal'], fontSize=8, leading=10, fontName='Helvetica')
    style_cell = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=8, leading=10, alignment=TA_CENTER)
    style_cell_left = ParagraphStyle('CellLeft', parent=styles['Normal'], fontSize=8, leading=10, alignment=TA_LEFT)

    story = []
    story.append(Paragraph("SIGE 360 - BOLETIM ESCOLAR OFICIAL", style_titulo))
    story.append(Paragraph(f"Ano Letivo: {turma.ano_letivo} | Documento Autêntico do Sistema Escolar", style_sub))
    story.append(Spacer(1, 0.2*cm))

    meta_dados = [
        [Paragraph("<b>Aluno(a):</b>", style_label), Paragraph(aluno.nome_completo.upper(), style_val), Paragraph("<b>Matrícula:</b>", style_label), Paragraph(matricula.numero_matricula or f"MAT-{aluno.id:04d}", style_val)],
        [Paragraph("<b>Escola:</b>", style_label), Paragraph(escola.nome, style_val), Paragraph("<b>Turma:</b>", style_label), Paragraph(f"{turma.nome} ({turma.turno})", style_val)],
        [Paragraph("<b>Etapa Ensino:</b>", style_label), Paragraph(turma.etapa_ensino, style_val), Paragraph("<b>Situação:</b>", style_label), Paragraph(f"<b>{matricula.status}</b>", style_val)],
    ]
    t_meta = Table(meta_dados, colWidths=[2.5*cm, 7.5*cm, 2.5*cm, 5.5*cm])
    t_meta.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f8f9fa')),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#ced4da')),
        ('INNERGRID', (0,0), (-1,-1), 0.3, colors.HexColor('#e9ecef')),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 0.3*cm))

    # Tabela de Notas por Disciplina
    headers = [Paragraph("<b>Componente Curricular</b>", style_cell_left)]
    for p in periodos:
        headers.append(Paragraph(f"<b>{p.nome[:6]}</b>", style_cell))
    headers.extend([Paragraph("<b>Média</b>", style_cell), Paragraph("<b>Faltas</b>", style_cell), Paragraph("<b>Situação</b>", style_cell)])

    dados_tabela = [headers]

    for disc in disciplinas:
        linha = [Paragraph(disc.nome, style_cell_left)]
        soma_notas = 0.0
        qtd_notas = 0
        total_faltas_disc = 0

        for p in periodos:
            nota_obj = AcadNota.query.filter_by(matricula_id=matricula.id, disciplina_id=disc.id, periodo_id=p.id).first()
            if nota_obj and nota_obj.nota_valor is not None:
                val = nota_obj.nota_valor
                soma_notas += val
                qtd_notas += 1
                total_faltas_disc += (nota_obj.faltas_bimestre or 0)
                txt_nota = f"{val:.1f}".replace('.', ',')
            else:
                txt_nota = "-"
            linha.append(Paragraph(txt_nota, style_cell))

        media_final = (soma_notas / qtd_notas) if qtd_notas > 0 else 0.0
        media_txt = f"{media_final:.1f}".replace('.', ',') if qtd_notas > 0 else "-"
        situacao_disc = "Aprovado" if media_final >= 6.0 else ("Em Curso" if qtd_notas < len(periodos) else "Retido")
        
        linha.extend([
            Paragraph(f"<b>{media_txt}</b>", style_cell),
            Paragraph(str(total_faltas_disc), style_cell),
            Paragraph(situacao_disc, style_cell)
        ])
        dados_tabela.append(linha)

    col_w = [6.0*cm] + [1.5*cm]*len(periodos) + [1.8*cm, 1.5*cm, 2.2*cm]
    t_notas = Table(dados_tabela, colWidths=col_w)
    ts = [
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1b4332')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#bc4749')),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]
    for r in range(1, len(dados_tabela)):
        if r % 2 == 0:
            ts.append(('BACKGROUND', (0, r), (-1, r), colors.HexColor('#f8f9fa')))
    t_notas.setStyle(TableStyle(ts))
    story.append(t_notas)
    story.append(Spacer(1, 0.6*cm))

    # Assinaturas
    t_ass = Table([
        ["_____________________________________________", "_____________________________________________"],
        [Paragraph("<b>Secretaria Escolar / Direção</b>", style_cell), Paragraph("<b>Responsável pelo Aluno</b>", style_cell)]
    ], colWidths=[9.0*cm, 9.0*cm])
    t_ass.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'CENTER')]))
    story.append(t_ass)

    doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape_moderno(c, d, "SIGE 360 - BOLETIM ESCOLAR"),
                     onLaterPages=lambda c, d: cabecalho_e_rodape_moderno(c, d, "SIGE 360 - BOLETIM ESCOLAR"))

    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Boletim_{aluno.id}.pdf'
    return response


@academico_bp.route('/turmas/<int:turma_id>/ata-pdf')
@role_required('admin', 'academico', 'RH')
def pdf_ata_resultados_finais(turma_id):
    turma = AcadTurma.query.get_or_404(turma_id)
    escola = turma.escola
    matriculas = AcadMatricula.query.filter_by(turma_id=turma_id).join(AcadAluno).order_by(AcadAluno.nome_completo).all()
    disciplinas = AcadDisciplina.query.order_by(AcadDisciplina.nome).all()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=1.0*cm, leftMargin=1.0*cm, topMargin=2.5*cm, bottomMargin=1.2*cm)
    styles = getSampleStyleSheet()
    
    style_tit = ParagraphStyle('TitAta', parent=styles['Heading1'], alignment=TA_CENTER, fontSize=12, leading=14, fontName='Helvetica-Bold', textColor=colors.HexColor('#004d40'))
    style_sub = ParagraphStyle('SubAta', parent=styles['Normal'], alignment=TA_CENTER, fontSize=8, leading=10)
    style_cell = ParagraphStyle('CellAta', parent=styles['Normal'], fontSize=7, leading=8.5, alignment=TA_CENTER)
    style_cell_left = ParagraphStyle('CellLeftAta', parent=styles['Normal'], fontSize=7, leading=8.5, alignment=TA_LEFT)

    story = []
    story.append(Paragraph(f"ATA DE RESULTADOS FINAIS E RENDIMENTO ESCOLAR — ANO LETIVO {turma.ano_letivo}", style_tit))
    story.append(Paragraph(f"Unidade Escolar: {escola.nome} | Turma: {turma.nome} | Etapa: {turma.etapa_ensino} | Turno: {turma.turno}", style_sub))
    story.append(Spacer(1, 0.2*cm))

    headers = [Paragraph("<b>Nº</b>", style_cell), Paragraph("<b>Nome do Aluno(a)</b>", style_cell_left)]
    for d in disciplinas:
        headers.append(Paragraph(f"<b>{d.nome[:8]}</b>", style_cell))
    headers.extend([Paragraph("<b>Total Faltas</b>", style_cell), Paragraph("<b>Resultado Final</b>", style_cell)])

    dados = [headers]

    for idx, mat in enumerate(matriculas, start=1):
        aluno = mat.aluno
        linha = [Paragraph(str(idx), style_cell), Paragraph(aluno.nome_completo, style_cell_left)]
        
        soma_geral = 0.0
        qtd_disc = 0
        faltas_aluno = 0

        for d in disciplinas:
            notas = AcadNota.query.filter_by(matricula_id=mat.id, disciplina_id=d.id).all()
            if notas:
                med = sum(n.nota_valor for n in notas if n.nota_valor is not None) / len(notas)
                faltas_aluno += sum(n.faltas_bimestre or 0 for n in notas)
                soma_geral += med
                qtd_disc += 1
                med_txt = f"{med:.1f}".replace('.', ',')
            else:
                med_txt = "-"
            linha.append(Paragraph(med_txt, style_cell))

        res_final = mat.status
        linha.extend([Paragraph(str(faltas_aluno), style_cell), Paragraph(f"<b>{res_final}</b>", style_cell)])
        dados.append(linha)

    col_w = [0.8*cm, 6.5*cm] + [2.0*cm]*len(disciplinas) + [1.8*cm, 3.0*cm]
    t_ata = Table(dados, colWidths=col_w)
    ts = [
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#004d40')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#6c757d')),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]
    for r in range(1, len(dados)):
        if r % 2 == 0:
            ts.append(('BACKGROUND', (0, r), (-1, r), colors.HexColor('#f8f9fa')))
    t_ata.setStyle(TableStyle(ts))
    story.append(t_ata)
    story.append(Spacer(1, 0.5*cm))

    t_ass = Table([
        ["_______________________________________", "_______________________________________", "_______________________________________"],
        [Paragraph("Secretário(a) Escolar", style_cell), Paragraph("Diretor(a) Escolar", style_cell), Paragraph("Presidente do Conselho de Classe", style_cell)]
    ], colWidths=[9.0*cm, 9.0*cm, 9.0*cm])
    t_ass.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'CENTER')]))
    story.append(t_ass)

    doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape_moderno(c, d, "ATA DE RESULTADOS FINAIS"),
                     onLaterPages=lambda c, d: cabecalho_e_rodape_moderno(c, d, "ATA DE RESULTADOS FINAIS"))

    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Ata_Resultados_Turma_{turma.id}.pdf'
    return response