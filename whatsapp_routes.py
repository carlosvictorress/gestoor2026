from flask import Blueprint, render_template, request, jsonify
# Importe aqui a conexão com seu banco ou seus models de transporte, se houver
# from models import Transporte 

whatsapp_bp = Blueprint('whatsapp', __name__)

@whatsapp_bp.route('/atendimento')
def chat_page():
    # Renderiza o arquivo que você já tem
    return render_template('chat/chatgestoor.html')

@whatsapp_bp.route('/atendimento/ask', methods=['POST'])
def ask():
    data = request.json
    pergunta = data.get("message", "").lower()
    
    # Lógica de resposta baseada no seu exemplo
    if "bom dia" in pergunta or "olá" in pergunta:
        resposta = "Bom dia! Sou o assistente do Gestor 360. Em que posso ajudá-lo hoje?"
    
    elif "ônibus" in pergunta or "onibus" in pergunta:
        resposta = "Boa pergunta! Existem vários ônibus escolares. Para eu te passar a rota exata, por favor, me informe o nome da **Escola**."
    
    elif "amando lima" in pergunta:
        # Aqui depois faremos uma busca no banco de dados real
        resposta = (
            "Perfeito! O ônibus que atende a Unidade Escolar Amando Lima é:\n\n"
            "🚌 **Veículo:** Amarelinho\n"
            "🆔 **Placa:** QRX-2G08\n"
            "👤 **Motorista:** Francisco Gonçalves\n"
            "⏰ **Ida:** 06:10 às 06:45\n"
            "⏰ **Volta:** 11:20 às 12:00\n\n"
            "Posso ajudar com mais alguma informação?"
        )
    else:
        resposta = "Ainda não entendi sua dúvida. Se for sobre transporte, tente dizer o nome da escola ou da rua."

    return jsonify({"response": resposta})