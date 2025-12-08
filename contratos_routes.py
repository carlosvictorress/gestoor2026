# contratos_routes.py

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, make_response
from functools import wraps
from extensions import db, bcrypt
from models import Servidor, Contrato
from datetime import datetime
import io
import locale
import os # <-- Adicionar import
from flask import current_app
from werkzeug.utils import secure_filename # <-- Adicionar import
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, BaseDocTemplate, Frame, PageTemplate, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from num2words import num2words
from utils import role_required

try:
    locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')
except locale.Error:
    print("Locale pt_BR.UTF-8 não encontrado, usando o padrão do sistema.")

contratos_bp = Blueprint(
    'contratos', 
    __name__, 
    template_folder='templates', 
    url_prefix='/contratos'
)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@contratos_bp.route('/')
@login_required
def gerenciar_contratos():
    q_servidor = request.args.get('q_servidor', '')
    query = Contrato.query
    if q_servidor:
        query = query.join(Servidor).filter(Servidor.nome.ilike(f'%{q_servidor}%'))
    
    contratos = query.order_by(Contrato.data_geracao.desc()).all()
    return render_template('gerenciar_contratos.html', contratos=contratos, q_servidor=q_servidor)

@contratos_bp.route('/api/servidor/<string:cpf>')
@login_required
@role_required('RH', 'admin')
def get_servidor_data(cpf):
    cpf_limpo = ''.join(filter(str.isdigit, cpf))
    servidor = Servidor.query.filter_by(cpf=cpf_limpo).first()
    
    if not servidor:
        return jsonify({'error': 'Servidor não encontrado.'}), 404
        
    vinculos_permitidos = ['Comissionado', 'Terceirizado', 'Outros']
    if servidor.tipo_vinculo not in vinculos_permitidos:
        return jsonify({'error': f'Geração de contrato disponível apenas para {", ".join(vinculos_permitidos)}. Vínculo atual: {servidor.tipo_vinculo}'}), 403

    servidor_dict = {c.name: getattr(servidor, c.name) for c in servidor.__table__.columns}
    if servidor_dict.get('data_nascimento'):
        servidor_dict['data_nascimento'] = servidor_dict['data_nascimento'].isoformat()
    if servidor_dict.get('data_inicio'):
        servidor_dict['data_inicio'] = servidor_dict['data_inicio'].isoformat()
    if servidor_dict.get('data_saida'):
        servidor_dict['data_saida'] = servidor_dict['data_saida'].isoformat()
        
    return jsonify(servidor_dict)

def gerar_numero_contrato(ano):
    ultimo_contrato_do_ano = Contrato.query.filter_by(ano=ano).order_by(Contrato.id.desc()).first()
    if ultimo_contrato_do_ano:
        ultimo_num = int(ultimo_contrato_do_ano.numero.split('/')[0])
        novo_num = ultimo_num + 1
    else:
        novo_num = 1
    return f"{novo_num:03d}/{ano}"

@contratos_bp.route('/gerar', methods=['POST'])
@login_required
@role_required('RH', 'admin')
def gerar_contrato():
    cpf = request.form.get('servidor_cpf')
    ano = int(request.form.get('ano_contrato'))
    
    servidor = Servidor.query.filter_by(cpf=cpf).first_or_404()
    numero_contrato = gerar_numero_contrato(ano)
    
    if not servidor.funcao or not servidor.remuneracao:
        flash('Atenção: A função ou a remuneração do servidor não estão preenchidas. O contrato será gerado com dados em falta.', 'warning')

    remuneracao_extenso = "zero reais"
    if servidor.remuneracao:
        try:
            remuneracao_float = float(servidor.remuneracao)
            reais = int(remuneracao_float)
            centavos = int(round((remuneracao_float - reais) * 100))
            remuneracao_extenso = num2words(reais, lang='pt_BR') + " reais"
            if centavos > 0:
                remuneracao_extenso += " e " + num2words(centavos, lang='pt_BR') + " centavos"
        except (ValueError, TypeError) as e:
            print(f"Erro ao converter remuneração para extenso: {e}")

    cnpj_municipio = "00.000.000/0001-00"
    endereco_municipio = "Praça da Independência, nº 123, Centro, Valença do Piauí - PI"
    secretaria_nome = "NOME COMPLETO DA SECRETÁRIA"

    texto_contrato = f"""
<title>CONTRATO ADMINISTRATIVO Nº {numero_contrato}</title>
<right_title>CONTRATO DE PRESTAÇÃO DE SERVIÇOS TEMPORÁRIO FIRMADO ENTRE O MUNICÍPIO DE VALENÇA DO PIAUÍ - SECRETARIA DE EDUCAÇÃO E {(servidor.nome or '[NOME EM FALTA]').upper()} PARA ATUAÇÃO DE {(servidor.funcao or '[FUNÇÃO EM FALTA]').upper()}, NOS TERMOS DO ART. 37, INCISO IX DA CONSTITUIÇÃO FEDERAL.</right_title>

<preamble>Pelo presente instrumento, de um lado, como CONTRATANTE, o MUNICÍPIO DE VALENÇA DO PIAUÍ, pessoa jurídica de direito público interno, inscrito no CNPJ sob o nº {cnpj_municipio}, com sede na {endereco_municipio}, neste ato representado pela Secretária Municipal de Educação, Sra. {secretaria_nome}, e de outro lado, como CONTRATADO(A), {servidor.nome or '[NOME EM FALTA]'}, {servidor.nacionalidade or 'brasileiro(a)'}, {servidor.estado_civil or '[ESTADO CIVIL EM FALTA]'}, portador(a) do RG nº {servidor.rg or '[RG EM FALTA]'} e do CPF nº {servidor.cpf}, residente e domiciliado(a) na {servidor.endereco or '[ENDEREÇO EM FALTA]'}.</preamble>

<clause_title>1 - DO OBJETO</clause_title>
<clause_body>CLÁUSULA PRIMEIRA - O objeto do presente contrato é a contratação de Servidor Temporário para atender ao Excepcional Interesse Público para a prestação de serviços na função de {servidor.funcao or '[FUNÇÃO EM FALTA]'}, atendido as determinações da Secretaria Municipal de Educação, conforme Constituição Federal, artigo 37, inciso IX.</clause_body>
<clause_body>PARÁGRAFO ÚNICO - Os trabalhos serão desenvolvidos em estrita observância às cláusulas deste contrato, principalmente no tocante às obrigações do CONTRATADO.</clause_body>

<clause_title>2 — DO PREÇO</clause_title>
<clause_body>CLÁUSULA SEGUNDA - O CONTRATANTE pagará ao CONTRATADO o valor mensal de R$ {servidor.remuneracao or 0:,.2f} ({remuneracao_extenso}). Pelos serviços contratados, estando incluídos nos mesmos todos os insumos, taxas, encargos e demais despesas.</clause_body>

<clause_title>3 — DA JORNADA DE TRABALHO</clause_title>
<clause_body>CLÁUSULA TERCEIRA - A jornada de trabalho do CONTRATADO durante a vigência do presente contrato é de {servidor.carga_horaria or '40'} (quarenta) horas semanais, regime de dedicação exclusiva, sob pena de rescisão contratual.</clause_body>

<clause_title>4 - DO PRAZO</clause_title>
<clause_body>CLÁUSULA QUARTA – O contratado trabalhará em caráter de excepcionalidade, contados a partir de 01 de março de {ano} a 20 de dezembro de {ano}.</clause_body>
<clause_body>PARÁGRAFO ÚNICO - O presente contrato poderá ser rescindido a qualquer tempo por acordo entre as partes, ou unilateralmente pela CONTRATANTE no caso do CONTRATADO deixar de cumprir qualquer uma das suas cláusulas, devendo prevalecer em todos os casos o interesse público.</clause_body>

<clause_title>5 — DA EXECUÇÃO DOS SERVIÇOS</clause_title>
<clause_body>CLÁUSULA QUINTA - Na execução dos serviços o CONTRATADO se obriga a respeitar, rigorosamente, durante o período de vigência deste contrato as normas de higiene e segurança, por cujos encargos responderão unilateralmente, devendo observar também os requisitos de qualidade, determinados pelo CONTRATANTE, através do setor responsável pela fiscalização, aprovação e liberação do serviço.</clause_body>

<clause_title>6 - DAS OBRIGAÇÕES DO CONTRATANTE</clause_title>
<clause_body>CLÁUSULA SEXTA - São obrigações do CONTRATANTE:</clause_body>
<clause_body>I - Fornecer elementos necessários à realização do objeto deste contrato;</clause_body>
<clause_body>II - Receber os serviços, procedendo-lhe a vistoria necessária e compatível com o objeto deste;</clause_body>
<clause_body>III - Efetuar a retenção da contribuição previdenciária obrigatória sobre sua remuneração, para os fins da Lei 8212/91 e Lei 8213/91.</clause_body>

<clause_title>7 - DAS OBRIGAÇÕES DO CONTRATADO</clause_title>
<clause_body>CLÁUSULA SÉTIMA - São obrigações do CONTRATADO:</clause_body>
<clause_body>I - Todas as despesas referentes ao objeto deste contrato, mão de obra, locomoção, seguro de acidente, impostos federais, estaduais e municipais, contribuições previdenciárias, encargos trabalhistas e quaisquer outras que forem devidas, relativamente à execução dos serviços ora contratado;</clause_body>
<clause_body>II - Executar serviços ora contratados com esmero e dentro da melhor técnica, responsabilizando-se por quaisquer erros, falhas ou imperfeições que por ventura ocorram;</clause_body>
<clause_body>III - Responsabilizar-se pelos danos causados diretamente à Administração ou a terceiros, decorrentes de seus serviços;</clause_body>
<clause_body>IV - Sujeitar-se à mais ampla e irrestrita fiscalização por parte do CONTRATANTE, prestando todos os esclarecimentos solicitados e atendendo as reclamações solicitadas.</clause_body>

<clause_title>8 - DA DOTAÇÃO ORÇAMENTÁRIA</clause_title>
<clause_body>CLÁUSULA OITAVA - As despesas decorrentes do presente contrato correrão por conta de recursos orçamentários consignados no Orçamento Anual especificamente de recursos da Secretaria de Educação.</clause_body>

<clause_title>9 — DAS PENALIDADES</clause_title>
<clause_body>CLÁUSULA NONA — Se o CONTRATADO não satisfazer os compromissos assumidos serão aplicadas as penalidades previstas na Lei Municipal n.º 861/97 (Estatuto dos Servidores Públicos do Município de Valença do Piauí).</clause_body>

<clause_title>10 - DA RESCISÃO</clause_title>
<clause_body>CLÁUSULA DÉCIMA - Este contrato estará rescindido, automaticamente:</clause_body>
<clause_body>a) No final do prazo estipulado na Cláusula Quarta, desde que não tenha ocorrido prorrogação;</clause_body>
<clause_body>b) Se a parte CONTRATADA incidir em qualquer das faltas arroladas no Estatuto dos Servidores Públicos do Município de Valença do Piauí.</clause_body>

<clause_title>11 - DAS DISPOSIÇÕES GERAIS</clause_title>
<clause_body>CLÁUSULA DÉCIMA PRIMEIRA - Além das cláusulas que compõem o presente contrato, ficam sujeitos também, as normas previstas na Lei Municipal n.º 861/97 (Estatuto dos Servidores Públicos do Município de Valença do Piauí).</clause_body>

<clause_title>12 - DO FORO</clause_title>
<clause_body>CLÁUSULA DÉCIMA SEGUNDA - Fica eleito o FORO da Comarca de Valença do Piauí — PI, com expressa renúncia de qualquer outro, para serem dirimidas quaisquer dúvidas pertinentes ao presente contrato.</clause_body>
<clause_body>As partes firmam o presente instrumento em 02 (duas) vias de igual teor e forma, obrigando-se por si e seus sucessores, ao fiel cumprimento do que ora ficou ajustado, elegendo-o.</clause_body>
"""
    
    novo_contrato = Contrato(
        numero=numero_contrato,
        ano=ano,
        servidor_cpf=cpf,
        conteudo=texto_contrato.strip()
    )
    
    db.session.add(novo_contrato)
    db.session.commit()
    
    flash(f'Contrato {novo_contrato.numero} gerado com sucesso!', 'success')
    return redirect(url_for('contratos.gerenciar_contratos'))

# --- INÍCIO DA ROTA ADICIONADA ---
@contratos_bp.route('/assinatura/<int:contrato_id>', methods=['POST'])
@login_required
@role_required('RH', 'admin')
def definir_assinatura(contrato_id):
    contrato = Contrato.query.get_or_404(contrato_id)
    tipo_assinatura = request.form.get('tipo_assinatura')
    
    contrato.assinatura_secretaria_tipo = tipo_assinatura
    
    if tipo_assinatura == 'imagem':
        ficheiro = request.files.get('assinatura_imagem')
        if ficheiro and ficheiro.filename != '':
            # Garante que a pasta de uploads exista
            # A pasta 'assinaturas' ficará dentro da pasta 'uploads' principal
            pasta_assinaturas = os.path.join(current_app.config['UPLOAD_FOLDER'], 'assinaturas')
            os.makedirs(pasta_assinaturas, exist_ok=True)
            
            nome_seguro = secure_filename(ficheiro.filename)
            # Cria um nome de arquivo único para evitar sobreposições
            nome_unico = f"contrato_{contrato_id}_{nome_seguro}"
            caminho_salvar = os.path.join(pasta_assinaturas, nome_unico)
            ficheiro.save(caminho_salvar)
            
            # Salva o nome do arquivo no banco de dados
            contrato.assinatura_secretaria_dados = nome_unico
        elif not contrato.assinatura_secretaria_dados:
            # Se o usuário selecionou 'imagem' mas não enviou um arquivo (e não havia um antes)
            flash('Para o tipo "Imagem", é necessário carregar um ficheiro de assinatura.', 'warning')
            return redirect(url_for('contratos.gerenciar_contratos'))

    else: # Se for 'manual' ou outro tipo
        contrato.assinatura_secretaria_dados = None
        
    db.session.commit()
    flash('Opção de assinatura atualizada com sucesso!', 'success')
    return redirect(url_for('contratos.gerenciar_contratos'))
# --- FIM DA ROTA ADICIONADA ---

def cabecalho_rodape(canvas, doc):
    canvas.saveState()
    logo_path = os.path.join(current_app.static_folder, 'img_contrato.jpg')
    if os.path.exists(logo_path):
        canvas.drawImage(logo_path, x=2*cm, y=A4[1] - 2.5*cm, width=17*cm, height=2*cm, preserveAspectRatio=True, mask='auto')
    else:
        canvas.setFont('Helvetica-Bold', 10)
        canvas.drawCentredString(A4[0] / 2, A4[1] - 1.5*cm, "Logótipo não encontrado em /static/img_contrato.jpg")
    canvas.setFont('Helvetica', 9)
    canvas.drawString(2*cm, 1.5*cm, f"Contrato Nº {doc.contrato_numero}")
    canvas.drawRightString(A4[0] - 2*cm, 1.5*cm, f"Página {doc.page}")
    canvas.restoreState()

@contratos_bp.route('/visualizar/<int:contrato_id>')
@login_required
@role_required('RH', 'admin')
def visualizar_contrato_pdf(contrato_id):
    contrato = Contrato.query.get_or_404(contrato_id)
    servidor = contrato.servidor
    
    buffer = io.BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=A4, leftMargin=2.5*cm, rightMargin=2.5*cm, topMargin=3.5*cm, bottomMargin=2.5*cm)
    
    doc.contrato_numero = contrato.numero
    
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='normal')
    template = PageTemplate(id='main', frames=[frame], onPage=cabecalho_rodape)
    doc.addPageTemplates([template])
    
    styles = getSampleStyleSheet()
    style_body = ParagraphStyle(name='Body', parent=styles['Normal'], alignment=TA_JUSTIFY, fontSize=12, leading=15, spaceAfter=12)
    style_preamble = ParagraphStyle(name='Preamble', parent=style_body, firstLineIndent=2*cm)
    style_title = ParagraphStyle(name='Title', parent=styles['h1'], alignment=TA_CENTER, fontSize=12, leading=14, spaceAfter=8, fontName='Helvetica-Bold')
    style_clause_title = ParagraphStyle(name='ClauseTitle', parent=styles['h2'], alignment=TA_LEFT, fontSize=12, leading=14, spaceBefore=10, spaceAfter=4, fontName='Helvetica-Bold')
    style_signature = ParagraphStyle(name='Signature', parent=styles['Normal'], alignment=TA_CENTER, fontSize=12, spaceBefore=6)
    
    story = []
    
    linhas = contrato.conteudo.split('\n')
    
    for linha in linhas:
        linha_strip = linha.strip()
        if not linha_strip:
            continue
        
        if linha_strip.startswith('<title>'):
            texto = linha_strip.replace('<title>', '').replace('</title>', '')
            story.append(Paragraph(texto, style_title))
        elif linha_strip.startswith('<right_title>'):
            texto = linha_strip.replace('<right_title>', '').replace('</right_title>', '')
            style_right_justified = ParagraphStyle(name='RightJustified', parent=styles['Normal'], alignment=TA_JUSTIFY, fontSize=10, leading=12)
            p = Paragraph(texto, style_right_justified)
            tabela = Table([[None, p]], colWidths=[7*cm, 9*cm])
            tabela.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP')]))
            story.append(tabela)
            story.append(Spacer(1, 0.5*cm))
        elif linha_strip.startswith('<preamble>'):
            texto = linha_strip.replace('<preamble>', '').replace('</preamble>', '')
            story.append(Paragraph(texto, style_preamble))
        elif linha_strip.startswith('<clause_title>'):
            texto = linha_strip.replace('<clause_title>', '').replace('</clause_title>', '')
            story.append(Paragraph(texto, style_clause_title))
        elif linha_strip.startswith('<clause_body>'):
            texto = linha_strip.replace('<clause_body>', '').replace('</clause_body>', '')
            story.append(Paragraph(texto, style_body))
        else:
            story.append(Paragraph(linha_strip, style_body))

    story.append(Spacer(1, 1*cm))

    # --- LÓGICA PARA ADICIONAR A IMAGEM DA ASSINATURA ---
    if contrato.assinatura_secretaria_tipo == 'imagem' and contrato.assinatura_secretaria_dados:
        caminho_assinatura = os.path.join(current_app.config['UPLOAD_FOLDER'], 'assinaturas', contrato.assinatura_secretaria_dados)
        if os.path.exists(caminho_assinatura):
            # Adiciona a imagem da assinatura centralizada
            img = Image(caminho_assinatura, width=5*cm, height=2.5*cm, hAlign='CENTER')
            story.append(img)
            # Adiciona um espaçamento negativo para a linha ficar mais próxima
            story.append(Spacer(1, -0.5*cm)) 
        else:
            story.append(Paragraph("<i>[Imagem da assinatura não encontrada]</i>", style_signature))
            
    story.append(Paragraph("_" * 60, style_signature))
    story.append(Paragraph("Município de Valença do Piauí – SECRETÁRIA DE EDUCAÇÃO", style_signature))
    story.append(Spacer(1, 2*cm))
    story.append(Paragraph("_" * 60, style_signature))
    story.append(Paragraph(servidor.nome, style_signature))
    story.append(Paragraph("Contratado(a)", style_signature))

    doc.build(story)
    
    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=contrato_{contrato.numero.replace("/", "-")}.pdf'
    return response
    
@contratos_bp.route('/excluir/<int:contrato_id>')
@login_required
@role_required('RH', 'admin')
def excluir_contrato(contrato_id):
    # Procura o contrato pelo ID ou retorna um erro 404 se não encontrar
    contrato_para_excluir = Contrato.query.get_or_404(contrato_id)
    
    try:
        # Guarda o número para a mensagem de log
        numero_contrato = contrato_para_excluir.numero
        
        db.session.delete(contrato_para_excluir)
        db.session.commit()
        
        # Você pode querer registrar essa ação no seu sistema de logs, se tiver um
        # registrar_log(f'Excluiu o contrato nº {numero_contrato}.')
        
        flash(f'Contrato nº {numero_contrato} excluído com sucesso!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir o contrato: {e}', 'danger')

    return redirect(url_for('contratos.gerenciar_contratos'))
    
    
@contratos_bp.route('/editar/<int:contrato_id>', methods=['GET', 'POST'])
@login_required
@role_required('RH', 'admin')
def editar_contrato(contrato_id):
    contrato = Contrato.query.get_or_404(contrato_id)
    
    if request.method == 'POST':
        try:
            # Pega o conteúdo do textarea do formulário
            contrato.conteudo = request.form.get('conteudo')
            
            db.session.commit()
            
            flash(f'Contrato nº {contrato.numero} atualizado com sucesso!', 'success')
            return redirect(url_for('contratos.gerenciar_contratos'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar o contrato: {e}', 'danger')
            return redirect(url_for('contratos.editar_contrato', contrato_id=contrato_id))

    # Se for um GET, apenas exibe a página de edição
    return render_template('editar_contrato.html', contrato=contrato)
