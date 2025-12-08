# Arquivo: whatsapp_routes.py (CORREÇÃO FINAL DE ENTREGA)

from flask import Blueprint, request, jsonify
from .agente_transporte import roteador_intencoes, menu_principal 
# Importação de funções de consulta para uso na lógica

whatsapp_bp = Blueprint('whatsapp', __name__, url_prefix='/whatsapp')

# Endpoint para a W-API enviar as mensagens POST
@whatsapp_bp.route('/webhook', methods=['POST'])
def whatsapp_webhook():
    try:
        data = request.get_json() or request.form
        
        # --- 1. LÓGICA DE EXTRAÇÃO ROBUSA (para ler o texto) ---
        msg_content = data.get('msgContent', {})
        
        # Padrão 1: Mensagens de texto simples ('conversation')
        mensagem_recebida = msg_content.get('conversation')
        
        # Padrão 2: Texto em mídias (caption)
        if not mensagem_recebida:
             mensagem_recebida = msg_content.get('imageMessage', {}).get('caption') or \
                                msg_content.get('videoMessage', {}).get('caption') or \
                                msg_content.get('extendedTextMessage', {}).get('text')
                                
        # Ignora eventos que não são mensagens de texto válidas
        if not mensagem_recebida:
            # Retorna JSON vazio ou 200 OK para ignorar eventos de áudio, status, etc.
            return jsonify({}), 200

        # 2. Processa a mensagem
        resposta_texto = roteador_intencoes(mensagem_recebida)

        # 3. RETORNO CRÍTICO: Usa a chave 'message' (ou 'content') que a API espera
        return jsonify({
            "status": "success",
            # Troca reply_text por message para corresponder ao padrão da API
            "message": resposta_texto 
        }), 200

    except Exception as e:
        print(f"ERRO CRÍTICO NO WEBHOOK: {e}")
        # Retorna a mensagem de erro no formato que a API pode entregar
        return jsonify({
            "status": "error", 
            "message": "Desculpe, ocorreu um erro no servidor de consulta. Tente digitar 'Olá' mais tarde."
        }), 200