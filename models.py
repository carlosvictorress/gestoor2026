# models.py

# from .extensions import db
from datetime import datetime, time, date
from extensions import db
# --- SEUS MODELOS ---


class Log(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    username = db.Column(db.String(80), nullable=False)
    action = db.Column(db.String(255), nullable=False)
    ip_address = db.Column(db.String(45))


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)

    # --- ALTERAÇÃO AQUI ---
    # Remova o 'unique=True' desta linha
    email = db.Column(db.String(120), nullable=False) 
    # --------------------

    password_hash = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="operador")

    # Relação com a Secretaria
    secretaria_id = db.Column(db.Integer, db.ForeignKey('secretaria.id'), nullable=True) 
    secretaria = db.relationship('Secretaria', backref='usuarios')

    # Relação com as Notas
    notas = db.relationship(
        "Nota", backref="autor", lazy=True, cascade="all, delete-orphan"
    )

    # --- ADICIONE ESTE BLOCO NO FINAL DA CLASSE ---
    __table_args__ = (
        db.UniqueConstraint('email', name='uq_user_email'),
    )


class Servidor(db.Model):
    __tablename__ = "servidor"
    num_contrato = db.Column(db.String(50), primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=True)
    rg = db.Column(db.String(20))
    data_nascimento = db.Column(db.Date, nullable=True)
    nome_mae = db.Column(db.String(200), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    pis_pasep = db.Column(db.String(20), nullable=True)
    tipo_vinculo = db.Column(db.String(50), nullable=True)
    local_trabalho = db.Column(db.String(150), nullable=True)
    escola_id = db.Column(db.Integer, db.ForeignKey('escola.id'), nullable=True)
    escola_vinculada = db.relationship('Escola', foreign_keys=[escola_id])
    classe_nivel = db.Column(db.String(50), nullable=True)
    num_contra_cheque = db.Column(db.String(50), nullable=True)
    nacionalidade = db.Column(db.String(50), default="brasileira")
    estado_civil = db.Column(db.String(50), default="solteiro(a)")
    telefone = db.Column(db.String(20))
    endereco = db.Column(db.String(250))
    funcao = db.Column(db.String(100))
    lotacao = db.Column(db.String(100))
    carga_horaria = db.Column(db.String(50))
    remuneracao = db.Column(db.Float)
    dados_bancarios = db.Column(db.String(200))
    data_inicio = db.Column(db.Date, nullable=True)
    data_saida = db.Column(db.Date, nullable=True)
    observacoes = db.Column(db.Text, nullable=True)
    foto_filename = db.Column(db.String(500), nullable=True)
    face_encoding = db.Column(db.Text, nullable=True) # Armazenará o encoding como um JSON string
    num_contrato_gerado = db.Column(db.String(10), unique=True, nullable=True)
    
    # Relação com a Secretaria
    secretaria_id = db.Column(db.Integer, db.ForeignKey('secretaria.id'), nullable=True) 
    secretaria = db.relationship('Secretaria', backref='servidores_vinculados')# Alterado para True temporariamente

    # Relações existentes
    documentos = db.relationship(
        "Documento", backref="servidor", lazy=True, cascade="all, delete-orphan"
    )


class Veiculo(db.Model):
    placa = db.Column(db.String(10), primary_key=True)
    modelo = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    ano_fabricacao = db.Column(db.Integer)
    ano_modelo = db.Column(db.Integer)
    orgao = db.Column(db.String(150))
    secretaria_id = db.Column(db.Integer, db.ForeignKey('secretaria.id'), nullable=False)
    secretaria = db.relationship('Secretaria', backref='veiculos')
    abastecimentos = db.relationship(
        "Abastecimento", backref="veiculo", lazy=True, cascade="all, delete-orphan"
    )
    manutencoes = db.relationship(
        "Manutencao",
        backref="veiculo_manutencao",
        lazy=True,
        cascade="all, delete-orphan",
    )
    # CORREÇÃO: Adicionado o relacionamento inverso para a rota
    rota = db.relationship("RotaTransporte", back_populates="veiculo", uselist=False)
    autorizacao_detran = db.Column(db.String(50), nullable=True)
    validade_autorizacao = db.Column(db.Date, nullable=True)
    renavam = db.Column(db.String(50), nullable=True)
    certificado_tacografo = db.Column(db.String(50), nullable=True)
    data_emissao_tacografo = db.Column(db.Date, nullable=True)
    validade_tacografo = db.Column(db.Date, nullable=True)


class Abastecimento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    quilometragem = db.Column(db.Float, nullable=False)
    tipo_combustivel = db.Column(db.String(50), nullable=False)
    litros = db.Column(db.Float, nullable=False)
    valor_litro = db.Column(db.Float, nullable=False)
    valor_total = db.Column(db.Float, nullable=False)
    veiculo_placa = db.Column(
        db.String(10), db.ForeignKey("veiculo.placa"), nullable=False
    )
    # servidor_cpf = db.Column(db.String(14), db.ForeignKey('servidor.cpf'), nullable=False)
    # motorista = db.relationship('Servidor', backref=db.backref('abastecimentos', lazy=True))

    motorista_id = db.Column(db.Integer, db.ForeignKey("motorista.id"), nullable=False)
    motorista = db.relationship(
        "Motorista", backref=db.backref("abastecimentos", lazy=True)
    )


class Manutencao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Date, nullable=False)
    quilometragem = db.Column(db.Float, nullable=False)
    tipo_servico = db.Column(db.String(150), nullable=False)
    custo = db.Column(db.Float, nullable=False)
    descricao = db.Column(db.Text, nullable=True)
    oficina = db.Column(db.String(150), nullable=True)
    veiculo_placa = db.Column(
        db.String(10), db.ForeignKey("veiculo.placa"), nullable=False
    )


class Requerimento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    autoridade_dirigida = db.Column(db.String(200), nullable=False)
    servidor_cpf = db.Column(
        db.String(14), db.ForeignKey("servidor.cpf"), nullable=False
    )
    servidor = db.relationship(
        "Servidor", backref=db.backref("requerimentos", lazy=True)
    )
    tipo_documento = db.Column(db.String(50), default='Requerimento')
    natureza = db.Column(db.String(100), nullable=False)
    natureza_outro = db.Column(db.String(255), nullable=True)
    data_admissao = db.Column(db.Date, nullable=True)
    data_inicio_requerimento = db.Column(db.Date, nullable=False)
    duracao = db.Column(db.String(50), nullable=True)
    periodo_aquisitivo = db.Column(db.String(20), nullable=True)
    data_retorno_trabalho = db.Column(db.Date, nullable=True)
    data_conclusao = db.Column(db.Date, nullable=True)
    informacoes_complementares = db.Column(db.Text, nullable=True)
    parecer_juridico = db.Column(db.Text, nullable=True)
    data_criacao = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    status = db.Column(db.String(50), nullable=False, default="Em Análise")


class Nota(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(120), nullable=False)
    conteudo = db.Column(db.Text, nullable=True)
    data_criacao = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Documento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    upload_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    servidor_id = db.Column(
        db.String(50), db.ForeignKey("servidor.num_contrato"), nullable=False
    )


class License(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    expiration_date = db.Column(db.DateTime, nullable=False)
    renewal_key = db.Column(db.String(100), unique=True, nullable=True)


class Ponto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    servidor_cpf = db.Column(
        db.String(14), db.ForeignKey("servidor.cpf"), nullable=False
    )
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    tipo = db.Column(db.String(10), nullable=False)

    # --- VERIFIQUE SE ESTES CAMPOS ESTÃO AQUI ---
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    foto_filename = db.Column(db.String(100), nullable=True)
    escola_id = db.Column(db.Integer, db.ForeignKey("escola.id"), nullable=True)
    # -------------------------------------------

    servidor_ponto = db.relationship(
        "Servidor",
        backref=db.backref("pontos", lazy=True, cascade="all, delete-orphan"),
    )
    escola = db.relationship("Escola", backref=db.backref("pontos", lazy=True))


class RotaTransporte(db.Model):
    __tablename__ = "rota_transporte"
    id = db.Column(db.Integer, primary_key=True)
    motorista_cpf = db.Column(
        db.String(14), db.ForeignKey("servidor.cpf"), nullable=False
    )
    veiculo_placa = db.Column(
        db.String(10), db.ForeignKey("veiculo.placa"), nullable=False
    )
    monitor_cpf = db.Column(db.String(14), db.ForeignKey("servidor.cpf"), nullable=True)
    escolas_manha = db.Column(db.String(500))
    itinerario_manha = db.Column(db.Text)
    qtd_alunos_manha = db.Column(db.Integer, default=0)
    coordenadas_manha = db.Column(db.Text, nullable=True)
    escolas_tarde = db.Column(db.String(500))
    itinerario_tarde = db.Column(db.Text)
    qtd_alunos_tarde = db.Column(db.Integer, default=0)
    coordenadas_tarde = db.Column(db.Text, nullable=True)

    horario_saida_manha = db.Column(db.Time, nullable=True)
    horario_volta_manha = db.Column(db.Time, nullable=True)
    horario_saida_tarde = db.Column(db.Time, nullable=True)
    horario_volta_tarde = db.Column(db.Time, nullable=True)

    trechos = db.relationship(
        "TrechoRota", backref="rota", lazy=True, cascade="all, delete-orphan"
    )

    # CORREÇÃO: Adicionados os relacionamentos para criar os atributos .motorista, .monitor e .veiculo
    motorista = db.relationship(
        "Servidor", foreign_keys=[motorista_cpf], backref="rotas_como_motorista"
    )
    monitor = db.relationship(
        "Servidor", foreign_keys=[monitor_cpf], backref="rotas_como_monitor"
    )
    veiculo = db.relationship(
        "Veiculo", back_populates="rota", foreign_keys=[veiculo_placa]
    )

    alunos = db.relationship(
        "AlunoTransporte", backref="rota", lazy=True, cascade="all, delete-orphan"
    )


class TrechoRota(db.Model):
    """ Tabela de detalhes de quilometragem por trecho em uma rota. """
    __tablename__ = "trecho_rota"
    id = db.Column(db.Integer, primary_key=True)
    rota_id = db.Column(db.Integer, db.ForeignKey("rota_transporte.id"), nullable=False)
    turno = db.Column(db.String(10), nullable=False)  # 'manha' ou 'tarde'
    tipo_viagem = db.Column(db.String(10), nullable=False)  # 'ida' ou 'volta'
    distancia_km = db.Column(db.Float, nullable=False)
    descricao = db.Column(db.String(200), nullable=True)


class AlunoTransporte(db.Model):
    __tablename__ = "aluno_transporte"
    id = db.Column(db.Integer, primary_key=True)
    nome_completo = db.Column(db.String(200), nullable=False)
    data_nascimento = db.Column(db.Date, nullable=False)
    ano_estudo = db.Column(
        db.String(50), nullable=False
    )  # Ex: "5º Ano", "Ensino Médio 2º Ano"
    turno = db.Column(db.String(20), nullable=False)  # "Manhã" ou "Tarde"
    escola = db.Column(db.String(200), nullable=False)
    zona = db.Column(db.String(20), nullable=False)
    nome_responsavel = db.Column(db.String(200), nullable=False)
    telefone_responsavel = db.Column(db.String(20), nullable=False)
    endereco_aluno = db.Column(db.String(300), nullable=False)
    rota_id = db.Column(db.Integer, db.ForeignKey("rota_transporte.id"), nullable=False)
    sexo = db.Column(db.String(20), nullable=True)  # Ex: Masculino, Feminino
    cor = db.Column(db.String(50), nullable=True)  # Ex: Branca, Parda, Preta, etc.
    nivel_ensino = db.Column(db.String(100), nullable=True)
    possui_deficiencia = db.Column(db.Boolean, default=False)
    tipo_deficiencia = db.Column(
        db.String(200), nullable=True
    )  # Campo para descrever a deficiência


class Protocolo(db.Model):
    __tablename__ = "protocolo"
    id = db.Column(db.Integer, primary_key=True)
    numero_protocolo = db.Column(db.String(20), unique=True, nullable=False)
    assunto = db.Column(db.String(300), nullable=False)
    tipo_documento = db.Column(db.String(100), nullable=False)
    interessado = db.Column(db.String(200), nullable=False)
    setor_origem = db.Column(db.String(150), nullable=False)
    setor_atual = db.Column(db.String(150), nullable=False)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default="Aberto")
    motivo_cancelamento = db.Column(db.Text, nullable=True)
    tramitacoes = db.relationship(
        "Tramitacao", backref="protocolo", lazy=True, cascade="all, delete-orphan"
    )
    anexos = db.relationship(
        "Anexo", backref="protocolo", lazy=True, cascade="all, delete-orphan"
    )


class Tramitacao(db.Model):
    __tablename__ = "tramitacao"
    id = db.Column(db.Integer, primary_key=True)
    protocolo_id = db.Column(db.Integer, db.ForeignKey("protocolo.id"), nullable=False)
    setor_origem = db.Column(db.String(150), nullable=False)
    setor_destino = db.Column(db.String(150), nullable=False)
    data_envio = db.Column(db.DateTime, default=datetime.utcnow)
    despacho = db.Column(db.Text, nullable=True)
    usuario_responsavel = db.Column(db.String(100))


class Anexo(db.Model):
    __tablename__ = "anexo"
    id = db.Column(db.Integer, primary_key=True)
    protocolo_id = db.Column(db.Integer, db.ForeignKey("protocolo.id"), nullable=False)
    nome_arquivo = db.Column(db.String(255), nullable=False)
    nome_original = db.Column(db.String(255), nullable=False)
    data_upload = db.Column(db.DateTime, default=datetime.utcnow)


class Contrato(db.Model):
    __tablename__ = "contrato"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(20), unique=True, nullable=False)
    ano = db.Column(db.Integer, nullable=False)
    servidor_cpf = db.Column(
        db.String(14), db.ForeignKey("servidor.cpf"), nullable=False
    )
    conteudo = db.Column(db.Text, nullable=False)
    assinatura_secretaria_tipo = db.Column(db.String(20), default="manual")
    assinatura_secretaria_dados = db.Column(db.String(255), nullable=True)
    data_geracao = db.Column(db.DateTime, default=datetime.utcnow)

    # --- ALTERAÇÃO APLICADA AQUI ---
    # Remova o 'cascade' para que a exclusão não seja automática.
    servidor = db.relationship("Servidor", backref="contratos")


class Patrimonio(db.Model):
    __tablename__ = "patrimonio"

    # Campos de Identificação
    id = db.Column(db.Integer, primary_key=True)
    numero_patrimonio = db.Column(db.String(50), unique=True, nullable=False) # Também conhecido como Tombamento
    descricao = db.Column(db.String(300), nullable=False) # Nome principal do bem
    categoria = db.Column(db.String(100), nullable=True) # Ex: Móveis, Eletrônicos, Veículos
    
    # Controle de Estado e Uso (Essencial para o novo Design)
    status = db.Column(db.String(50), nullable=False, default="Ativo") # Ativo, Manutenção, Baixado
    estado_conservacao = db.Column(db.String(50), default="Bom") # Bom, Regular, Inservível
    situacao_uso = db.Column(db.String(50), default="Em uso") # Em uso, Almoxarifado, Manutenção
    
    # Detalhes Técnicos
    marca = db.Column(db.String(100))
    modelo = db.Column(db.String(100))
    localizacao = db.Column(db.String(200), nullable=False) # Ex: "SEMED - Sala 01"
    
    # Dados Financeiros e de Registro
    data_aquisicao = db.Column(db.Date, nullable=True)
    valor_aquisicao = db.Column(db.Float, nullable=True)
    observacoes = db.Column(db.Text, nullable=True)
    foto_url = db.Column(db.String(500)) # Link para a imagem no Supabase Storage
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)

    # Relacionamento com o Servidor (Responsável atual)
    servidor_responsavel_cpf = db.Column(
        db.String(14), db.ForeignKey("servidor.cpf"), nullable=True
    )
    responsavel = db.relationship(
        "Servidor", backref=db.backref("patrimonios_responsaveis", lazy=True)
    )

    # Histórico de movimentações
    movimentacoes = db.relationship(
        "MovimentacaoPatrimonio",
        backref="patrimonio",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f'<Patrimonio {self.numero_patrimonio} - {self.descricao}>'


class MovimentacaoPatrimonio(db.Model):
    __tablename__ = "movimentacao_patrimonio"
    id = db.Column(db.Integer, primary_key=True)
    patrimonio_id = db.Column(
        db.Integer, db.ForeignKey("patrimonio.id"), nullable=False
    )
    data_movimentacao = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # De onde veio
    local_origem = db.Column(db.String(200), nullable=False)
    responsavel_anterior_cpf = db.Column(
        db.String(14), db.ForeignKey("servidor.cpf"), nullable=True
    )

    # Para onde foi
    local_destino = db.Column(db.String(200), nullable=False)
    responsavel_novo_cpf = db.Column(
        db.String(14), db.ForeignKey("servidor.cpf"), nullable=True
    )

    # Quem registrou a movimentação
    usuario_registro = db.Column(db.String(80), nullable=False)

    responsavel_anterior = db.relationship(
        "Servidor", foreign_keys=[responsavel_anterior_cpf]
    )
    responsavel_novo = db.relationship("Servidor", foreign_keys=[responsavel_novo_cpf])


class Escola(db.Model):
    __tablename__ = "escola"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False, unique=True)
    codigo_inep = db.Column(db.String(20), unique=True, nullable=True, index=True)
    endereco = db.Column(db.String(300))
    telefone = db.Column(db.String(20))

    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    diretor_cpf = db.Column(db.String(14), db.ForeignKey("servidor.cpf"))
    responsavel_merenda_cpf = db.Column(db.String(14), db.ForeignKey("servidor.cpf"))
    status = db.Column(
        db.String(20), nullable=False, default="Ativa"
    )  # Ativa / Inativa

    diretor = db.relationship("Servidor", foreign_keys=[diretor_cpf])
    responsavel_merenda = db.relationship(
        "Servidor", foreign_keys=[responsavel_merenda_cpf]
    )


class ProdutoMerenda(db.Model):
    __tablename__ = "produto_merenda"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False, unique=True)
    unidade_medida = db.Column(db.String(20), nullable=False)  # Ex: KG, L, Unid
    categoria = db.Column(db.String(100))  # Ex: Estocáveis, Proteína, Hortifrúti
    estoque_atual = db.Column(db.Float, nullable=False, default=0.0)
    
    # --- NOVOS CAMPOS PROFISSIONAIS ---
    estoque_minimo = db.Column(db.Float, default=10.0) # Para alertas
    tipo_armazenamento = db.Column(db.String(50)) # Ex: Seco, Refrigerado, Congelado
    perecivel = db.Column(db.Boolean, default=True)
    
    # Dados Nutricionais (Baseado em 100g ou 100ml) - Essencial para o PNAE
    calorias = db.Column(db.Float, default=0.0)
    proteinas = db.Column(db.Float, default=0.0)
    carboidratos = db.Column(db.Float, default=0.0)
    lipidios = db.Column(db.Float, default=0.0) # Gorduras
    
    # Relacionamentos
    
class EstoqueMovimento(db.Model):
    __tablename__ = "estoque_movimento"
    id = db.Column(db.Integer, primary_key=True)
    produto_id = db.Column(
        db.Integer, db.ForeignKey("produto_merenda.id"), nullable=False
    )
    tipo = db.Column(db.String(10), nullable=False)  # 'Entrada' ou 'Saída'
    quantidade = db.Column(db.Float, nullable=False)
    data_movimento = db.Column(db.DateTime, default=datetime.utcnow)

    # Para Entradas
    fornecedor = db.Column(db.String(200))
    lote = db.Column(db.String(50))
    data_validade = db.Column(db.Date)

    # Para Saídas
    solicitacao_id = db.Column(db.Integer, db.ForeignKey("solicitacao_merenda.id"))

    # Rastreabilidade
    usuario_responsavel = db.Column(db.String(80), nullable=False)

    produto = db.relationship("ProdutoMerenda", backref="movimentos")
    solicitacao = db.relationship("SolicitacaoMerenda")


class SolicitacaoMerenda(db.Model):
    __tablename__ = "solicitacao_merenda"
    id = db.Column(db.Integer, primary_key=True)
    escola_id = db.Column(db.Integer, db.ForeignKey("escola.id"), nullable=False)
    data_solicitacao = db.Column(db.DateTime, default=datetime.utcnow)
    data_entrega = db.Column(db.DateTime)
    status = db.Column(
        db.String(50), default="Pendente"
    )  # Pendente, Autorizada, Entregue, Cancelada

    # Rastreabilidade completa
    solicitante_cpf = db.Column(
        db.String(14), db.ForeignKey("servidor.cpf"), nullable=False
    )
    autorizador_cpf = db.Column(db.String(14), db.ForeignKey("servidor.cpf"))
    entregador_cpf = db.Column(db.String(14), db.ForeignKey("servidor.cpf"))

    escola = db.relationship("Escola", backref="solicitacoes")
    itens = db.relationship(
        "SolicitacaoItem", backref="solicitacao", cascade="all, delete-orphan"
    )

    solicitante = db.relationship("Servidor", foreign_keys=[solicitante_cpf])
    autorizador = db.relationship("Servidor", foreign_keys=[autorizador_cpf])
    entregador = db.relationship("Servidor", foreign_keys=[entregador_cpf])


class SolicitacaoItem(db.Model):
    __tablename__ = "solicitacao_item"
    id = db.Column(db.Integer, primary_key=True)
    solicitacao_merenda_id = db.Column(
        db.Integer, db.ForeignKey("solicitacao_merenda.id"), nullable=False
    )
    produto_id = db.Column(
        db.Integer, db.ForeignKey("produto_merenda.id"), nullable=False
    )
    quantidade_solicitada = db.Column(db.Float, nullable=False)

    produto = db.relationship("ProdutoMerenda")


class Cardapio(db.Model):
    __tablename__ = "cardapio"
    id = db.Column(db.Integer, primary_key=True)
    escola_id = db.Column(db.Integer, db.ForeignKey("escola.id"), nullable=False)
    # --- ALTERAÇÃO APLICADA AQUI ---
    mes = db.Column(
        db.Integer, nullable=False
    )  # Armazena o número do mês (ex: 8 para Agosto)
    ano = db.Column(db.Integer, nullable=False)  # Armazena o ano (ex: 2025)
    observacoes = db.Column(db.Text)

    escola = db.relationship("Escola", backref="cardapios")
    pratos = db.relationship(
        "PratoDiario", backref="cardapio", cascade="all, delete-orphan"
    )
    historico = db.relationship(
        "HistoricoCardapio", backref="cardapio", cascade="all, delete-orphan"
    )

    # Garante que só exista um cardápio por escola para cada mês/ano
    __table_args__ = (
        db.UniqueConstraint("escola_id", "mes", "ano", name="_escola_mes_ano_uc"),
    )


class PratoDiario(db.Model):
    __tablename__ = "prato_diario"
    id = db.Column(db.Integer, primary_key=True)
    cardapio_id = db.Column(db.Integer, db.ForeignKey("cardapio.id"), nullable=False)
    # --- ALTERAÇÃO APLICADA AQUI ---
    data_prato = db.Column(db.Date, nullable=False)  # Armazena a data exata do prato
    nome_prato = db.Column(db.String(200), nullable=False)


class HistoricoCardapio(db.Model):
    __tablename__ = "historico_cardapio"
    id = db.Column(db.Integer, primary_key=True)
    cardapio_id = db.Column(db.Integer, db.ForeignKey("cardapio.id"), nullable=False)
    usuario = db.Column(db.String(80), nullable=False)
    data_modificacao = db.Column(db.DateTime, default=datetime.utcnow)
    descricao_mudanca = db.Column(
        db.Text, nullable=False
    )  # Ex: "Alterou o prato de Terça-feira de 'Macarronada' para 'Arroz com Frango'."


# models.py

# ... (todos os seus outros modelos) ...


class Motorista(db.Model):
    __tablename__ = "motorista"  # Renomeia a tabela no banco de dados
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)

    # NOVOS CAMPOS ADICIONADOS
    tipo_vinculo = db.Column(db.String(50))  # Efetivo, Contratado, Terceirizado
    secretaria = db.Column(db.String(150))  # Secretaria a que pertence

    rg = db.Column(db.String(20))
    cpf = db.Column(db.String(14), unique=True)
    endereco = db.Column(db.String(250))
    telefone = db.Column(db.String(20))
    cnh_numero = db.Column(db.String(20))
    cnh_categoria = db.Column(db.String(5))
    cnh_validade = db.Column(db.Date)
    rota_descricao = db.Column(db.String(300))
    turno = db.Column(db.String(50))
    veiculo_modelo = db.Column(db.String(100))
    veiculo_ano = db.Column(db.Integer)
    veiculo_placa = db.Column(db.String(10))
    documentos = db.relationship(
        "DocumentoMotorista",
        backref="motorista",
        lazy=True,
        cascade="all, delete-orphan",
    )


class DocumentoMotorista(db.Model):
    __tablename__ = "documento_motorista"  # Renomeia a tabela no banco de dados
    id = db.Column(db.Integer, primary_key=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey("motorista.id"), nullable=False)
    tipo_documento = db.Column(db.String(100), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    upload_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class GAM(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    servidor_num_contrato = db.Column(db.String(50), db.ForeignKey("servidor.num_contrato"), nullable=False)
    servidor = db.relationship("Servidor", backref="gams", foreign_keys=[servidor_num_contrato])

    # --- CAMPOS DETALHADOS DO FORMULÁRIO ---
    # Seção "Observações da Chefia"
    texto_inicial_observacoes = db.Column(db.Text, nullable=True) # "A requerente é efetiva..."

    # Seção do Laudo Médico
    data_laudo = db.Column(db.Date, nullable=True) # "datada do dia..."
    medico_laudo = db.Column(db.String(200), nullable=True) # "o médico psiquiatra..."
    dias_afastamento_laudo = db.Column(db.Integer, nullable=True) # "recomenda 90 dias..."
    justificativa_laudo = db.Column(db.Text, nullable=True) # "pois declara que a servidora..."

    # Seção do CID e Encaminhamento
    cid10 = db.Column(db.String(20), nullable=True)
    
    data_emissao = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    status = db.Column(db.String(50), nullable=False, default="Emitida")
    
    
    
    
class Secretaria(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), unique=True, nullable=False)

    def __repr__(self):
        return f'<Secretaria {self.nome}>'    

		
class Fornecedor(db.Model):
    __tablename__ = 'almox_fornecedor'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    cnpj = db.Column(db.String(20), unique=True, nullable=True)
    endereco = db.Column(db.String(300))
    telefone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    dados_bancarios = db.Column(db.Text)
    
    materiais = db.relationship('Material', back_populates='fornecedor_padrao')
    movimentacoes = db.relationship('MovimentoEstoque', back_populates='fornecedor')

class Material(db.Model):
    __tablename__ = 'almox_material'
    id = db.Column(db.Integer, primary_key=True)
    codigo_interno = db.Column(db.String(50), unique=True, nullable=True)
    descricao = db.Column(db.String(300), nullable=False)
    unidade_medida = db.Column(db.String(20), nullable=False)
    categoria = db.Column(db.String(100))
    estoque_atual = db.Column(db.Float, nullable=False, default=0.0)
    estoque_minimo = db.Column(db.Float, default=0.0)
    estoque_maximo = db.Column(db.Float, default=0.0)
    localizacao_fisica = db.Column(db.String(100))
    ultimo_custo = db.Column(db.Float, default=0.0)
    codigo_barras = db.Column(db.String(100), unique=True, nullable=True)
    
    fornecedor_padrao_id = db.Column(db.Integer, db.ForeignKey('almox_fornecedor.id'), nullable=True)
    fornecedor_padrao = db.relationship('Fornecedor', back_populates='materiais')

    movimentacoes = db.relationship('MovimentoEstoque', backref='material', lazy=True)

class Requisicao(db.Model):
    __tablename__ = 'almox_requisicao'
    id = db.Column(db.Integer, primary_key=True)
    data_solicitacao = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    status = db.Column(db.String(50), nullable=False, default='Pendente') # Ex: Pendente, Aprovada, Atendida, Recusada
    justificativa = db.Column(db.Text)
    
    secretaria_solicitante_id = db.Column(db.Integer, db.ForeignKey('secretaria.id'), nullable=False)
    secretaria_solicitante = db.relationship('Secretaria', backref='requisicoes_almoxarifado')
    
    usuario_solicitante_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    usuario_solicitante = db.relationship('User', backref='requisicoes_almoxarifado')

    itens = db.relationship('RequisicaoItem', backref='requisicao', lazy='dynamic', cascade="all, delete-orphan")

class RequisicaoItem(db.Model):
    __tablename__ = 'almox_requisicao_item'
    id = db.Column(db.Integer, primary_key=True)
    quantidade_solicitada = db.Column(db.Float, nullable=False)
    quantidade_atendida = db.Column(db.Float, default=0.0)
    
    requisicao_id = db.Column(db.Integer, db.ForeignKey('almox_requisicao.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('almox_material.id'), nullable=False)
    material = db.relationship('Material')

class MovimentoEstoque(db.Model):
    __tablename__ = 'almox_movimento_estoque'
    id = db.Column(db.Integer, primary_key=True)
    data_movimento = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    tipo_movimento = db.Column(db.String(50), nullable=False) # Ex: 'Entrada NF', 'Saída Requisição', 'Ajuste Inventário'
    quantidade = db.Column(db.Float, nullable=False)
    valor_unitario = db.Column(db.Float)
    justificativa = db.Column(db.Text)
    nota_fiscal = db.Column(db.String(100))

    material_id = db.Column(db.Integer, db.ForeignKey('almox_material.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    usuario = db.relationship('User', backref='movimentos_estoque')
    
    fornecedor_id = db.Column(db.Integer, db.ForeignKey('almox_fornecedor.id'), nullable=True)
    fornecedor = db.relationship('Fornecedor', back_populates='movimentacoes')
    
    requisicao_item_id = db.Column(db.Integer, db.ForeignKey('almox_requisicao_item.id'), nullable=True)
    requisicao_item = db.relationship('RequisicaoItem', backref='movimentos')
		
# ==========================================================
# MÓDULO DE GESTÃO ACADÊMICA (ALINHADO AO CENSO ESCOLAR)
# ==========================================================

class AcadAluno(db.Model):
    __tablename__ = 'acad_aluno'
    id = db.Column(db.Integer, primary_key=True)
    # --- Dados de Identificação (Censo) ---
    nome_completo = db.Column(db.String(200), nullable=False, index=True)
    data_nascimento = db.Column(db.Date, nullable=False)
    sexo = db.Column(db.String(20)) # Masculino, Feminino
    cor_raca = db.Column(db.String(50)) # Branca, Preta, Parda, Amarela, Indígena
    filiacao_1 = db.Column(db.String(200)) # Nome da mãe/pai/responsável 1
    filiacao_2 = db.Column(db.String(200)) # Nome da mãe/pai/responsável 2
    nacionalidade = db.Column(db.String(50), default='Brasileira')
    cpf = db.Column(db.String(14), unique=True, nullable=True, index=True)
    id_inep = db.Column(db.String(20), unique=True, nullable=True) # Código do Aluno no Censo
    
    # --- Dados de Contato e Endereço ---
    nome_responsavel = db.Column(db.String(200))
    telefone_responsavel = db.Column(db.String(20))
    endereco = db.Column(db.String(300))
    
    # --- Necessidades Especiais (Censo) ---
    necessidade_especial = db.Column(db.Boolean, default=False)
    tipo_necessidade = db.Column(db.Text) # Campo para descrever (ex: Baixa Visão, Surdez, TDAH)

    status = db.Column(db.String(50), nullable=False, default='Ativo') # Ativo, Inativo, Transferido
    
    matriculas = db.relationship('AcadMatricula', back_populates='aluno', cascade="all, delete-orphan")

class AcadTurma(db.Model):
    __tablename__ = 'acad_turma'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False) # Ex: "1º Ano - A"
    ano_letivo = db.Column(db.Integer, nullable=False)
    turno = db.Column(db.String(50), nullable=False) # Manhã, Tarde, Noite, Integral
    etapa_ensino = db.Column(db.String(100), nullable=False) # Ed. Infantil, Fundamental I, etc.
    modalidade = db.Column(db.String(100), nullable=False) # Regular, EJA, AEE
    vagas = db.Column(db.Integer, nullable=False, default=30)
    
    escola_id = db.Column(db.Integer, db.ForeignKey('escola.id'), nullable=False)
    escola = db.relationship('Escola', back_populates='turmas')
    
    matriculas = db.relationship('AcadMatricula', back_populates='turma', cascade="all, delete-orphan")

class AcadMatricula(db.Model):
    __tablename__ = 'acad_matricula'
    id = db.Column(db.Integer, primary_key=True)
    data_matricula = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    status = db.Column(db.String(50), nullable=False, default='Cursando') # Cursando, Transferido, Desistente, Aprovado, Reprovado
    
    aluno_id = db.Column(db.Integer, db.ForeignKey('acad_aluno.id'), nullable=False)
    turma_id = db.Column(db.Integer, db.ForeignKey('acad_turma.id'), nullable=False)
    
    aluno = db.relationship('AcadAluno', back_populates='matriculas')
    turma = db.relationship('AcadTurma', back_populates='matriculas')

# Adiciona a relação de volta da Escola para as Turmas
Escola.turmas = db.relationship('AcadTurma', order_by=AcadTurma.id, back_populates='escola')		


# (Substitua o bloco do Módulo Académico que você adicionou antes por este)

# ==========================================================
# MÓDULO DE GESTÃO ACADÊMICA (ALINHADO AO CENSO ESCOLAR)
# ==========================================================

# Tabela de associação para ligar Professores e Disciplinas a uma Turma
acad_turma_disciplinas_professores = db.Table('acad_turma_disciplinas_professores',
    db.Column('turma_id', db.Integer, db.ForeignKey('acad_turma.id'), primary_key=True),
    db.Column('disciplina_id', db.Integer, db.ForeignKey('acad_disciplina.id'), primary_key=True),
    # CORREÇÃO: Aponta para a chave primária correta (String) de Servidor
    db.Column('professor_num_contrato', db.String(50), db.ForeignKey('servidor.num_contrato'), primary_key=True)
)

class AcadDisciplina(db.Model):
    __tablename__ = 'acad_disciplina'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    area_conhecimento = db.Column(db.String(100))

class AcadPeriodo(db.Model):
    __tablename__ = 'acad_periodo'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False) # Ex: "1º Bimestre", "2º Trimestre"
    ano_letivo = db.Column(db.Integer, nullable=False, index=True)
    data_inicio = db.Column(db.Date)
    data_fim = db.Column(db.Date)

class AcadNota(db.Model):
    __tablename__ = 'acad_nota'
    id = db.Column(db.Integer, primary_key=True)
    valor = db.Column(db.Float, nullable=False)

    matricula_id = db.Column(db.Integer, db.ForeignKey('acad_matricula.id'), nullable=False)
    disciplina_id = db.Column(db.Integer, db.ForeignKey('acad_disciplina.id'), nullable=False)
    periodo_id = db.Column(db.Integer, db.ForeignKey('acad_periodo.id'), nullable=False)
    # CORREÇÃO: Aponta para a chave primária correta (String) de Servidor
    professor_num_contrato = db.Column(db.String(50), db.ForeignKey('servidor.num_contrato'), nullable=False)

    # Relações para fácil acesso
    matricula = db.relationship('AcadMatricula', backref='notas')
    disciplina = db.relationship('AcadDisciplina')
    periodo = db.relationship('AcadPeriodo')
    # CORREÇÃO: Define a foreign_keys para a relação com Servidor
    professor = db.relationship('Servidor', foreign_keys=[professor_num_contrato])

    # Garante que um aluno só tem uma nota por disciplina/período
    __table_args__ = (db.UniqueConstraint('matricula_id', 'disciplina_id', 'periodo_id', name='_nota_unica_uc'),)

# --- ADIÇÕES A MODELOS EXISTENTES ---

# Adicione/substitua esta relação dentro da sua classe AcadTurma
AcadTurma.disciplinas_professores = db.relationship('Servidor',
    secondary=acad_turma_disciplinas_professores,
    backref=db.backref('turmas_lecionadas', lazy='dynamic'),
    lazy='dynamic'
)

# ==========================================================
# MÓDULO DE ATENDIMENTO EDUCACIONAL ESPECIALIZADO (CAEE)
# ==========================================================

class CaeeAluno(db.Model):
    """
    Prontuário digital do aluno em atendimento no CAEE.
    Este modelo é INDEPENDENTE do AcadAluno.
    """
    __tablename__ = 'caee_aluno'
    id = db.Column(db.Integer, primary_key=True)
    
    # --- Dados de Identificação ---
    nome_completo = db.Column(db.String(200), nullable=False, index=True)
    data_nascimento = db.Column(db.Date, nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=True, index=True)
    escola_origem = db.Column(db.String(200), nullable=True) # "Dados Escolares"
    cid_diagnostico = db.Column(db.String(20), nullable=True, index=True) # "CID / diagnóstico"
    necessidade_especifica = db.Column(db.String(200), nullable=True) # "TEA, TDAH, etc."
    
    # --- Dados de Filiação / Contato ---
    nome_responsavel = db.Column(db.String(200), nullable=False)
    telefone_responsavel = db.Column(db.String(20), nullable=False)
    endereco = db.Column(db.String(300))
    
    # --- Dados do Prontuário ---
    status = db.Column(db.String(50), nullable=False, default='Em Avaliação') # Ex: Em Avaliação, Ativo, Fila de Espera, Desligado
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Formulário longo para a entrevista com a família
    anamnese = db.Column(db.Text, nullable=True) 
    
    # Campo para a(s) hipótese(s) diagnóstica(s)
    hipotese_diagnostica = db.Column(db.Text, nullable=True) 
    
    # Relacionamento com a Secretaria (para seu sistema multi-secretaria)
    secretaria_id = db.Column(db.Integer, db.ForeignKey('secretaria.id'), nullable=False)
    secretaria = db.relationship('Secretaria', backref='alunos_caee')
    
    planos = db.relationship('CaeePlanoAtendimento', backref='aluno', lazy=True, cascade="all, delete-orphan")
    
    # Req #3: Laudos, pareceres, relatórios anexados
    laudos = db.relationship('CaeeLaudo', backref='aluno', lazy=True, cascade="all, delete-orphan")
    # Req #6: Evolução pedagógica por período
    relatorios_periodicos = db.relationship('CaeeRelatorioPeriodico', backref='aluno', lazy=True, cascade="all, delete-orphan")

    # (Futuramente, adicionaremos laudos e planos aqui)
    # laudos = db.relationship('CaeeLaudo', backref='aluno', lazy=True, cascade="all, delete-orphan")
    # plano = db.relationship('CaeePlanoAtendimento', backref='aluno', uselist=False, cascade="all, delete-orphan")


class CaeeProfissional(db.Model):
    """
    Cadastro da equipe multidisciplinar do CAEE.
    Este modelo é INDEPENDENTE do Servidor.
    """
    __tablename__ = 'caee_profissional'
    id = db.Column(db.Integer, primary_key=True)
    
    # --- Dados Pessoais / Profissionais ---
    nome_completo = db.Column(db.String(200), nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=False, index=True)
    telefone = db.Column(db.String(20), nullable=True)
    
    especialidade = db.Column(db.String(100), nullable=False) # Ex: Psicopedagogo, Fonoaudiólogo, T.O.
    registro_conselho = db.Column(db.String(50), nullable=True) # Ex: CREFONO 12345
    
    status = db.Column(db.String(50), nullable=False, default='Ativo') # Ex: Ativo, Inativo, Férias
    
    # Relacionamento com a Secretaria
    secretaria_id = db.Column(db.Integer, db.ForeignKey('secretaria.id'), nullable=False)
    secretaria_prof = db.relationship('Secretaria', backref='profissionais_caee')
    
    planos_atendimento = db.relationship('CaeePlanoAtendimento', backref='profissional', lazy=True)
    
    # (Futuramente, adicionaremos a agenda e os planos aqui)
    # planos_atendimento = db.relationship('CaeePlanoAtendimento', backref='profissional', lazy=True)
    
class CaeePlanoAtendimento(db.Model):
    """
    O PAI - Plano de Atendimento Individual.
    Esta é a tabela que LIGA um Aluno a um Profissional.
    """
    __tablename__ = 'caee_plano_atendimento'
    id = db.Column(db.Integer, primary_key=True)

    # --- Chaves Estrangeiras (As "LIGAÇÕES") ---
    aluno_id = db.Column(db.Integer, db.ForeignKey('caee_aluno.id'), nullable=False) # SEM unique=True
     # 'unique=True' garante 1 aluno por plano
    profissional_id = db.Column(db.Integer, db.ForeignKey('caee_profissional.id'), nullable=False)

    # --- Dados do Plano ---
    status_plano = db.Column(db.String(50), nullable=False, default='Ativo') # Ex: Ativo, Pausado, Concluído
    data_inicio = db.Column(db.Date, nullable=False, default=datetime.utcnow)

    frequencia_semanal = db.Column(db.Integer, default=1) # Ex: 1, 2, 3 vezes por semana
    duracao_sessao_min = db.Column(db.Integer, default=50) # Ex: 50 minutos

    objetivos_gerais = db.Column(db.Text, nullable=True)
    objetivos_especificos = db.Column(db.Text, nullable=True)
    metodologia = db.Column(db.Text, nullable=True)
    
    sessoes = db.relationship('CaeeSessao', backref='plano', lazy=True, cascade="all, delete-orphan")

    # (Futuramente, adicionaremos as sessões aqui)
    # sessoes = db.relationship('CaeeSessao', backref='plano', lazy=True, cascade="all, delete-orphan")
    
class CaeeSessao(db.Model):
    """
    Representa um único atendimento (sessão) do PAI.
    Este é o "Diário de Bordo" do profissional.
    """
    __tablename__ = 'caee_sessao'
    id = db.Column(db.Integer, primary_key=True)

    # --- Chave Estrangeira (A "LIGAÇÃO") ---
    plano_id = db.Column(db.Integer, db.ForeignKey('caee_plano_atendimento.id'), nullable=False)

    # --- Dados da Sessão ---
    data_sessao = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    presenca = db.Column(db.Boolean, nullable=False, default=True) # True = Presente, False = Falta

    atividades_realizadas = db.Column(db.Text, nullable=True)
    observacoes_evolucao = db.Column(db.Text, nullable=True)

    # Guarda quem foi o profissional que registrou (para histórico)
    profissional_nome = db.Column(db.String(200), nullable=False)
    
class CaeeLaudo(db.Model):
    """
    Req #3: Armazena os arquivos de laudo (PDFs, imagens) anexados
    ao prontuário de um aluno do CAEE.
    """
    __tablename__ = 'caee_laudo'
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('caee_aluno.id'), nullable=False)
    
    nome_original = db.Column(db.String(255), nullable=False) # Ex: "laudo_neuro.pdf"
    filename_seguro = db.Column(db.String(255), nullable=False) # Ex: "caee/uuid-abc.pdf"
    descricao = db.Column(db.String(300), nullable=True) # Ex: "Laudo neurológico Dr. João"
    data_upload = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    uploader_nome = db.Column(db.String(200), nullable=False) # Nome de quem fez o upload

class CaeeRelatorioPeriodico(db.Model):
    """
    Req #6: Armazena a Evolução Pedagógica Periódica (ex: Semestral).
    É diferente do Diário de Bordo (CaeeSessao).
    """
    __tablename__ = 'caee_relatorio_periodico'
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('caee_aluno.id'), nullable=False)
    profissional_id = db.Column(db.Integer, db.ForeignKey('caee_profissional.id'), nullable=False)
    
    periodo = db.Column(db.String(50), nullable=False) # Ex: "1º Semestre 2025"
    data_relatorio = db.Column(db.DateTime, default=datetime.utcnow)
    
    # O parecer/relatório pedagógico
    relatorio_evolucao = db.Column(db.Text, nullable=False) 
    
    profissional = db.relationship('CaeeProfissional', backref='relatorios_periodicos')    
    
class CaeeLinhaTempo(db.Model):
    """
    Registra a evolução do aluno entre os profissionais (O Fluxo/Encaminhamento).
    Representa cada 'círculo' no desenho do PDF.
    """
    __tablename__ = 'caee_linha_tempo'
    id = db.Column(db.Integer, primary_key=True)
    
    aluno_id = db.Column(db.Integer, db.ForeignKey('caee_aluno.id'), nullable=False)
    
    # Quem encaminhou (Origem)
    profissional_origem_id = db.Column(db.Integer, db.ForeignKey('caee_profissional.id'), nullable=True)
    
    # Para quem foi encaminhado ou quem atendeu (Destino/Atual)
    profissional_destino_id = db.Column(db.Integer, db.ForeignKey('caee_profissional.id'), nullable=True)
    
    # Qual a especialidade/etapa (Ex: "Triagem Social", "Atendimento Psicológico")
    etapa = db.Column(db.String(100), nullable=False)
    
    data_evento = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='Pendente') # Pendente, Em Andamento, Concluído
    observacao = db.Column(db.Text, nullable=True) # Motivo do encaminhamento

    # Relacionamentos
    aluno = db.relationship('CaeeAluno', backref='linha_tempo')
    profissional_origem = db.relationship('CaeeProfissional', foreign_keys=[profissional_origem_id])
    profissional_destino = db.relationship('CaeeProfissional', foreign_keys=[profissional_destino_id])    


class FiscalContrato(db.Model):
    """ Tabela principal de cadastro e visão geral do contrato. """
    __tablename__ = 'fiscal_contrato'
    id = db.Column(db.Integer, primary_key=True)

    # --- Dados de Identificação ---
    num_contrato = db.Column(db.String(50), unique=True, nullable=False)
    ano = db.Column(db.Integer, nullable=False)
    tipo = db.Column(db.String(100), nullable=False) 
    objeto = db.Column(db.Text, nullable=False)
    processo_licitatorio = db.Column(db.String(150), nullable=True) 

    # --- Dados da Contratada ---
    empresa_contratada = db.Column(db.String(200), nullable=False)
    cnpj = db.Column(db.String(20), nullable=False)
    representante_empresa = db.Column(db.String(150), nullable=True)

    # --- Valores e Vigência ---
    valor_total = db.Column(db.Float, nullable=False)
    valor_mensal_parcela = db.Column(db.Float, nullable=True)
    vigencia_inicio = db.Column(db.Date, nullable=False)
    vigencia_fim = db.Column(db.Date, nullable=False)

    # --- Status e Vínculo ---
    situacao = db.Column(db.String(50), default='Ativo', nullable=False)

    secretaria_id = db.Column(db.Integer, db.ForeignKey('secretaria.id'), nullable=False)

    # Relações:
    anexos = db.relationship('FiscalAnexo', backref='contrato', lazy=True, cascade="all, delete-orphan")
    atestos = db.relationship('FiscalAtestoMensal', backref='contrato', lazy=True, cascade="all, delete-orphan")
    ocorrencias = db.relationship('FiscalOcorrencia', backref='contrato', lazy=True, cascade="all, delete-orphan")


class FiscalAnexo(db.Model):
    """ Documentos (Edital, Contrato PDF, etc.) anexados ao FiscalContrato. """
    __tablename__ = 'fiscal_anexo'
    id = db.Column(db.Integer, primary_key=True)

    contrato_id = db.Column(db.Integer, db.ForeignKey('fiscal_contrato.id'), nullable=False)

    tipo_documento = db.Column(db.String(100), nullable=False) 
    nome_original = db.Column(db.String(255), nullable=False)
    filename_seguro = db.Column(db.String(255), nullable=False) 

    data_upload = db.Column(db.DateTime, default=datetime.utcnow)

# Nota: As classes FiscalAtestoMensal e FiscalOcorrencia ainda não foram adicionadas, mas a FiscalContrato já faz referência a elas. Isso pode causar um erro.

# --- A ÚLTIMA CORREÇÃO NECESSÁRIA ---
# Para evitar um erro de NameError nas classes citadas acima, precisamos adicioná-las (vazias) agora.

class FiscalAtestoMensal(db.Model):
    """ Registro de Execução Mensal/Atesto (Req 133-147). """
    __tablename__ = 'fiscal_atesto_mensal'
    id = db.Column(db.Integer, primary_key=True)
    
    contrato_id = db.Column(db.Integer, db.ForeignKey('fiscal_contrato.id'), nullable=False)
    
    # --- Dados do Atesto ---
    mes_competencia = db.Column(db.String(7), nullable=False) # Ex: 2025/01
    data_atesto = db.Column(db.DateTime, default=datetime.utcnow)
    descricao_servico = db.Column(db.Text, nullable=False)
    
    # Checklists e Conformidade
    conformidade = db.Column(db.String(50), nullable=False) # Aprovado, Reprovado, Aprovado com Ressalvas
    observacoes_fiscal = db.Column(db.Text, nullable=True)
    
    # Evidências e Localização
    evidencia_filename = db.Column(db.String(255), nullable=True) # Upload de evidências (Req 140)
    localizacao_gps = db.Column(db.String(100), nullable=True) # Coordenadas GPS opcionais (Req 141)
    
    # Status e Fiscal
    assinatura_fiscal = db.Column(db.String(100), nullable=False) # Nome do fiscal (Req 142)
    status_atesto = db.Column(db.String(50), default='Pendente', nullable=False) # (Req 143-147)
    
    checklist_respostas = db.relationship('FiscalChecklistResposta', backref='atesto', lazy=True, cascade="all, delete-orphan")


class FiscalOcorrencia(db.Model):
    """ Registro de falhas, problemas e não-conformidades (Req 148-166). """
    __tablename__ = 'fiscal_ocorrencia'
    id = db.Column(db.Integer, primary_key=True)
    
    contrato_id = db.Column(db.Integer, db.ForeignKey('fiscal_contrato.id'), nullable=False)
    
    # --- Dados da Ocorrência ---
    tipo_ocorrencia = db.Column(db.String(100), nullable=False) 
    data_hora = db.Column(db.DateTime, default=datetime.utcnow)
    descricao_detalhada = db.Column(db.Text, nullable=False)
    gravidade = db.Column(db.String(50), nullable=False) # Leve, Média, Grave (Req 162)
    
    evidencia_filename = db.Column(db.String(255), nullable=True) 
    local_ocorrencia = db.Column(db.String(200), nullable=True)
    
    responsavel_registro = db.Column(db.String(100), nullable=False)
    responsavel_analise = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(50), default='Aberta', nullable=False) # Aberta, Resolvida, Encerrada


class FiscalPenalidade(db.Model):
    """ Aplicação de sanções à empresa (Req 196-205). """
    __tablename__ = 'fiscal_penalidade'
    id = db.Column(db.Integer, primary_key=True)
    
    ocorrencia_id = db.Column(db.Integer, db.ForeignKey('fiscal_ocorrencia.id'), nullable=False)
    
    # --- Dados da Penalidade ---
    tipo = db.Column(db.String(100), nullable=False) # Advertência, Multa, Suspensão, Rescisão
    base_legal = db.Column(db.String(200), nullable=True)
    descricao_infracao = db.Column(db.Text, nullable=False)
    valor_multa = db.Column(db.Float, nullable=True)
    data_aplicacao = db.Column(db.Date, default=datetime.utcnow)
    responsavel = db.Column(db.String(100), nullable=False)
    documento_gerado = db.Column(db.String(255), nullable=True)
    
class FiscalChecklistModel(db.Model):
    """ Tabela mestre para os modelos de checklist (Ex: Transporte Escolar). """
    __tablename__ = 'fiscal_checklist_modelo'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False) # Ex: Checklist Transporte Escolar
    tipo_contrato_associado = db.Column(db.String(100), nullable=False) # Ex: Transporte, Merenda, Obra
    
    itens = db.relationship('FiscalChecklistItem', backref='modelo', lazy=True, cascade="all, delete-orphan")


class FiscalChecklistItem(db.Model):
    """ Item/Pergunta dentro de um modelo de Checklist (Ex: 'Pneus em bom estado?'). """
    __tablename__ = 'fiscal_checklist_item'
    id = db.Column(db.Integer, primary_key=True)
    modelo_id = db.Column(db.Integer, db.ForeignKey('fiscal_checklist_modelo.id'), nullable=False)
    
    descricao = db.Column(db.String(255), nullable=False) # A pergunta em si
    
    # Tipo de Resposta: 'Sim/Não', 'Texto', 'Numérico'
    tipo_resposta = db.Column(db.String(50), nullable=False, default='Sim/Não') 

class FiscalChecklistResposta(db.Model):
    """ Armazena a Resposta do Fiscal para um item específico durante um Atesto. """
    __tablename__ = 'fiscal_checklist_resposta'
    id = db.Column(db.Integer, primary_key=True)
    
    # Liga a Resposta ao Atesto mensal
    atesto_id = db.Column(db.Integer, db.ForeignKey('fiscal_atesto_mensal.id'), nullable=False)
    
    # Liga a Resposta à Pergunta original (para rastreabilidade)
    item_id = db.Column(db.Integer, db.ForeignKey('fiscal_checklist_item.id'), nullable=False)
    
    item = db.relationship('FiscalChecklistItem')
    
    # O Valor da Resposta (Sim/Não, ou texto, ou número)
    valor_resposta = db.Column(db.String(255), nullable=False)    

class FiscalNotaFiscal(db.Model):
    """ Registro de Notas Fiscais para rastrear o valor gasto. """
    __tablename__ = 'fiscal_nota_fiscal'
    id = db.Column(db.Integer, primary_key=True)
    
    contrato_id = db.Column(db.Integer, db.ForeignKey('fiscal_contrato.id'), nullable=False)
    
    # Detalhes da Nota
    numero_nf = db.Column(db.String(100), nullable=False)
    data_emissao = db.Column(db.Date, nullable=False)
    valor = db.Column(db.Float, nullable=False)
    
    # Referência (opcional, se quiser ligar ao Atesto Mensal)
    atesto_id = db.Column(db.Integer, db.ForeignKey('fiscal_atesto_mensal.id'), nullable=True)

    data_registro = db.Column(db.DateTime, default=datetime.utcnow)
    usuario_registro = db.Column(db.String(100), nullable=False) 
    
class DocumentoAssinado(db.Model):
    __tablename__ = "documento_assinado"
    id = db.Column(db.Integer, primary_key=True)
    
    # Dados do Documento
    nome_documento = db.Column(db.String(300), nullable=False)
    codigo_validade = db.Column(db.String(100), unique=True, nullable=False) # Chave para validação
    data_assinatura = db.Column(db.DateTime, default=datetime.utcnow)
    
    filename_seguro = db.Column(db.String(300), nullable=True)
    
    # Relações com quem assinou (o sistema insere a assinatura do Servidor)
    servidor_cpf = db.Column(db.String(14), db.ForeignKey("servidor.cpf"), nullable=False) # CPF do servidor que será inserido na assinatura
    servidor = db.relationship("Servidor", backref="documentos_selados")
    
    # Relação com o usuário que operou o sistema para fazer a assinatura
    usuario_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    assinante = db.relationship("User", backref="documentos_assinados_operados")
    
    # Posição da assinatura (Para futura expansão)
    pos_x = db.Column(db.Float, nullable=True) 
    pos_y = db.Column(db.Float, nullable=True)
    pagina = db.Column(db.Integer, default=1)

# --- IMPORTANTE ---
# Você também precisa garantir que a classe FiscalContrato tenha esta relação.
# Se FiscalContrato já estiver definida, adicione esta linha dentro dela:
# FiscalContrato.notas_fiscais = db.relationship('FiscalNotaFiscal', backref='contrato', lazy=True, cascade="all, delete-orphan")
    # Adicione esta relação na classe FiscalContrato (se ainda não estiver lá)
    # FiscalContrato.notas_fiscais = db.relationship('FiscalNotaFiscal', backref='contrato', lazy=True, cascade="all, delete-orphan")    
    
class AgricultorFamiliar(db.Model):
    __tablename__ = 'pnae_agricultor'
    id = db.Column(db.Integer, primary_key=True)
    
    # 1. Dados da Entidade/Fornecedor
    tipo_fornecedor = db.Column(db.String(50), nullable=False) # Individual, Grupo Formal, Informal, etc.
    razao_social = db.Column(db.String(200), nullable=False)
    nome_fantasia = db.Column(db.String(200))
    cpf_cnpj = db.Column(db.String(20), unique=True, nullable=False)
    
    # DAP/CAF
    dap_caf_numero = db.Column(db.String(50))
    dap_caf_tipo = db.Column(db.String(50))
    dap_caf_validade = db.Column(db.Date)
    
    # Representante Legal (para grupos)
    representante_nome = db.Column(db.String(200))
    representante_rg = db.Column(db.String(20))
    representante_rg_orgao = db.Column(db.String(20))
    representante_rg_emissao = db.Column(db.Date)
    
    telefone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    
    # 2. Endereço / Local de Produção
    endereco_completo = db.Column(db.String(300))
    zona = db.Column(db.String(20)) # Urbana / Rural
    comunidade = db.Column(db.String(150))
    latitude = db.Column(db.String(20))
    longitude = db.Column(db.String(20))
    descricao_propriedade = db.Column(db.Text)
    area_total_ha = db.Column(db.Float)
    
    # 5. Capacidade de Entrega (Resumo)
    frequencia_entrega = db.Column(db.String(100)) # Ex: Semanal
    dias_horarios = db.Column(db.String(200))
    possui_transporte = db.Column(db.Boolean, default=False)
    responsavel_entrega = db.Column(db.String(150))
    local_entrega_preferencia = db.Column(db.String(50)) # Escola, Depósito, Ambos
    
    # Indicadores e Controle
    status = db.Column(db.String(20), default='Ativo') # Ativo, Inadimplente, Pendente
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)

    # Relacionamentos
    documentos = db.relationship('DocumentoAgricultor', backref='agricultor', cascade="all, delete-orphan")
    contratos = db.relationship('ContratoPNAE', backref='agricultor', cascade="all, delete-orphan")

class DocumentoAgricultor(db.Model):
    __tablename__ = 'pnae_documento'
    id = db.Column(db.Integer, primary_key=True)
    agricultor_id = db.Column(db.Integer, db.ForeignKey('pnae_agricultor.id'), nullable=False)
    
    tipo = db.Column(db.String(100), nullable=False) # CND Federal, Projeto Venda, Comprovante Residência
    numero_ref = db.Column(db.String(50)) # Para número do projeto de venda ou certidão
    filename = db.Column(db.String(255), nullable=False)
    data_validade = db.Column(db.Date)
    data_upload = db.Column(db.DateTime, default=datetime.utcnow)

class ContratoPNAE(db.Model):
    __tablename__ = 'pnae_contrato'
    id = db.Column(db.Integer, primary_key=True)
    agricultor_id = db.Column(db.Integer, db.ForeignKey('pnae_agricultor.id'), nullable=False)
    
    numero_contrato = db.Column(db.String(50), nullable=False)
    chamada_publica = db.Column(db.String(100)) # Edital/Chamada
    
    data_inicio = db.Column(db.Date, nullable=False)
    data_termino = db.Column(db.Date, nullable=False)
    valor_total = db.Column(db.Float, nullable=False, default=0.0)
    
    observacoes = db.Column(db.Text)
    
    itens = db.relationship('ItemProjetoVenda', backref='contrato', cascade="all, delete-orphan")
    entregas = db.relationship('EntregaPNAE', backref='contrato', cascade="all, delete-orphan")

    @property
    def valor_executado(self):
        return sum(e.valor_total for e in self.entregas if e.status == 'Aprovado')

    @property
    def saldo(self):
        return self.valor_total - self.valor_executado

class ItemProjetoVenda(db.Model):
    """ Produtos acordados no Projeto de Venda (O que ele vai vender) """
    __tablename__ = 'pnae_item_projeto'
    id = db.Column(db.Integer, primary_key=True)
    contrato_id = db.Column(db.Integer, db.ForeignKey('pnae_contrato.id'), nullable=False)
    
    # 4. Produtos Disponíveis / Contratados
    nome_produto = db.Column(db.String(150), nullable=False)
    categoria = db.Column(db.String(50)) # Hortifruti, Proteína...
    unidade_medida = db.Column(db.String(20))
    
    quantidade_total = db.Column(db.Float, nullable=False) # Volume ofertado
    preco_unitario = db.Column(db.Float, nullable=False)
    
    tipo_producao = db.Column(db.String(50)) # Orgânica, Convencional
    sazonalidade = db.Column(db.String(200)) # Ex: "Jan, Fev, Mar"
    periodicidade = db.Column(db.String(50)) # Semanal, Quinzenal
    
class EntregaPNAE(db.Model):
    """ 7. Histórico de Entregas (Execução do contrato) """
    __tablename__ = 'pnae_entrega'
    id = db.Column(db.Integer, primary_key=True)
    contrato_id = db.Column(db.Integer, db.ForeignKey('pnae_contrato.id'), nullable=False)
    
    data_entrega = db.Column(db.Date, nullable=False)
    numero_nota_fiscal = db.Column(db.String(50))
    recibo_filename = db.Column(db.String(255)) # Upload
    
    responsavel_recebimento = db.Column(db.String(150))
    status = db.Column(db.String(20), default='Pendente') # Pendente, Aprovado, Rejeitado
    
    valor_total = db.Column(db.Float, default=0.0)
    observacoes = db.Column(db.Text)
    
    escola_id = db.Column(db.Integer, db.ForeignKey('escola.id'), nullable=True)
    
    # Itens desta entrega específica (JSON ou tabela separada, simplificado aqui como texto detalhado ou JSON)
    itens_json = db.Column(db.Text) # Ex: [{"produto": "Alface", "qtd": 10, "valor": 50.00}]
    
class ConfiguracaoPNAE(db.Model):
    __tablename__ = 'pnae_configuracao'
    id = db.Column(db.Integer, primary_key=True)
    ano = db.Column(db.Integer, unique=True, nullable=False)
    valor_total_repasse = db.Column(db.Float, nullable=False) # Valor total recebido do FNDE
    
    @property
    def meta_percentual(self):
        # Regra da Lei 15.226/2025
        if self.ano >= 2026:
            return 45.0
        return 30.0
        
    @property
    def valor_meta_minima(self):
        return self.valor_total_repasse * (self.meta_percentual / 100)    

class RelatorioTecnico(db.Model):
    __tablename__ = 'relatorios_tecnicos'

    id = db.Column(db.Integer, primary_key=True)
    tipo_documento = db.Column(db.String(50), nullable=False)
    numero_documento = db.Column(db.String(50), nullable=False)
    data_emissao = db.Column(db.Date, nullable=False)
    local_emissao = db.Column(db.String(100), nullable=False)
    
    vocativo = db.Column(db.String(50))
    destinatario_nome = db.Column(db.String(100), nullable=False)
    destinatario_cargo = db.Column(db.String(100))
    
    assunto = db.Column(db.String(200), nullable=False)
    corpo_texto = db.Column(db.Text, nullable=False)
    
    fecho = db.Column(db.String(50))
    responsavel_assinatura = db.Column(db.String(100), nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    anexos = db.relationship('RelatorioAnexo', backref='relatorio', lazy=True, cascade="all, delete-orphan")

class RelatorioAnexo(db.Model):
    __tablename__ = 'relatorio_anexo'
    id = db.Column(db.Integer, primary_key=True)
    relatorio_id = db.Column(db.Integer, db.ForeignKey('relatorios_tecnicos.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False) # Nome do arquivo salvo no servidor
    nome_original = db.Column(db.String(255), nullable=False) # Nome original do arquivo enviado
    descricao = db.Column(db.String(255)) # Descrição opcional (ex: "Foto da despensa")
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)

class ChamadoTecnico(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    # Vínculos com o que você já tem
    solicitante_cpf = db.Column(db.String(14), db.ForeignKey('servidor.cpf'), nullable=False)
    patrimonio_id = db.Column(db.Integer, db.ForeignKey('patrimonio.id'), nullable=True) # Se for um item específico
    escola_id = db.Column(db.Integer, db.ForeignKey('escola.id'), nullable=False)
    
    # Detalhes do Problema
    categoria = db.Column(db.String(50)) # Internet, Computador, Impressora, etc.
    descricao_problema = db.Column(db.Text, nullable=False)
    prioridade = db.Column(db.String(20), default='Média') # Baixa, Média, Alta
    
    # Fluxo de Atendimento
    status = db.Column(db.String(20), default='Aberto') # Aberto, Em Andamento, Aguardando Peça, Finalizado
    data_abertura = db.Column(db.DateTime, default=datetime.utcnow)
    tecnico_responsavel = db.Column(db.String(100)) # Nome do técnico que assumiu    
    
class RelatorioTecnico(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chamado_id = db.Column(db.Integer, db.ForeignKey('chamado_tecnico.id'), unique=True)
    
    # Diagnóstico e Solução
    laudo_tecnico = db.Column(db.Text, nullable=False)
    pecas_substituidas = db.Column(db.String(255)) # Ex: Teclado, Fonte, Memória
    situacao_final = db.Column(db.String(50)) # Resolvido, Perda Total (Sucateamento)
    data_conclusao = db.Column(db.DateTime, default=datetime.utcnow)    

class PedidoEmpresa(db.Model):
    __tablename__ = 'pedidos_empresa'
    id = db.Column(db.Integer, primary_key=True)
    data_pedido = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='Rascunho')  # Rascunho ou Enviado
    solicitante = db.Column(db.String(100))
    itens = db.relationship('PedidoEmpresaItem', backref='pedido', cascade="all, delete-orphan")

class PedidoEmpresaItem(db.Model):
    __tablename__ = 'pedidos_empresa_itens'
    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey('pedidos_empresa.id'))
    produto_id = db.Column(db.Integer, db.ForeignKey('produto_merenda.id'))
    quantidade = db.Column(db.Float, nullable=False)
    
    produto = db.relationship('ProdutoMerenda')    
    
