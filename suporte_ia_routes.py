# suporte_ia_routes.py
from flask import Blueprint, request, jsonify, session
from utils import login_required # Garantindo que só quem está logado use o suporte
import google.generativeai as genai
import os

suporte_ia_bp = Blueprint('suporte_ia', __name__)

# Configuração da IA (Use a chave que você já tem no .env)
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')

@suporte_ia_bp.route('/api/suporte', methods=['POST'])
@login_required
def suporte_chat():
    dados = request.json
    pergunta = dados.get('mensagem')
    
    # O "Cérebro" alimentado pelos seus arquivos
    prompt_contexto = f"""
    Você é o Assistente Especialista do sistema Gestoor360 de Valença do Piauí.
    Seu público: Funcionários públicos (usuários comuns, não admins).
    
    CONTEXTO DO SISTEMA:
    - Merenda: Registra entradas, solicitações das escolas e PNAE.
    - Almoxarifado: Controle de materiais e fornecedores.
    - RH: Servidores, Ponto Facial e Frequência.
    - Frota/Combustível: Abastecimento (badget vermelho se < 7km/L) e rotas no mapa.
    - Patrimônio: Registro de bens e etiquetas QR Code.

    REGRAS DE OURO:
    1. Se o usuário perguntar "como fazer X", explique o caminho dos menus.
    2. Nunca peça senhas ou dados sensíveis.
    3. Se o erro for técnico (banco de dados), peça para contatar Carlos Victor.
    4. Seja educado e direto.
    """

    try:
        response = model.generate_content(f"{prompt_contexto}\n\nUsuário pergunta: {pergunta}")
        return jsonify({"resposta": response.text})
    except Exception as e:
        return jsonify({"resposta": "Desculpe, tive um soluço técnico. Tente novamente em instantes."}), 500