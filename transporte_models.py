# transporte_models.py

from datetime import datetime
# Aponte para o novo arquivo 'extensions.py' para pegar o 'db'
from extensions import db 

# O resto do arquivo continua exatamente igual...

# Define a tabela para guardar as informações de cada rota
class RotaTransporte(db.Model):
    __tablename__ = 'rota_transporte'
    id = db.Column(db.Integer, primary_key=True)
    
    # Chaves para conectar com Motorista, Veículo e Monitor
    motorista_cpf = db.Column(db.String(14), db.ForeignKey('servidor.cpf'), nullable=False)
    veiculo_placa = db.Column(db.String(10), db.ForeignKey('veiculo.placa'), nullable=False)
    monitor_cpf = db.Column(db.String(14), db.ForeignKey('servidor.cpf'), nullable=True) # Opcional
    
    # Campos para os detalhes da rota
    escolas_manha = db.Column(db.String(500))
    itinerario_manha = db.Column(db.Text) # Bairros, ruas, etc.
    qtd_alunos_manha = db.Column(db.Integer, default=0)
    
    escolas_tarde = db.Column(db.String(500))
    itinerario_tarde = db.Column(db.Text)
    qtd_alunos_tarde = db.Column(db.Integer, default=0)

    # Relacionamento para acessar os alunos desta rota
    alunos = db.relationship('AlunoTransporte', backref='rota', lazy=True, cascade="all, delete-orphan")

# Define a tabela para guardar os dados de cada aluno transportado
class AlunoTransporte(db.Model):
    __tablename__ = 'aluno_transporte'
    id = db.Column(db.Integer, primary_key=True)
    nome_completo = db.Column(db.String(200), nullable=False)
    data_nascimento = db.Column(db.Date, nullable=False)
    escola = db.Column(db.String(200), nullable=False)
    zona = db.Column(db.String(20), nullable=False) # "Urbana" ou "Rural"
    nome_responsavel = db.Column(db.String(200), nullable=False)
    telefone_responsavel = db.Column(db.String(20), nullable=False)
    endereco_aluno = db.Column(db.String(300), nullable=False)
    
    # Chave para conectar o aluno à sua rota
    rota_id = db.Column(db.Integer, db.ForeignKey('rota_transporte.id'), nullable=False)
