# agente_transporte.py

from models import RotaTransporte, Servidor # Importamos apenas o necessário
from extensions import db
from sqlalchemy.orm import joinedload
from sqlalchemy import func

def menu_principal():
    """Retorna o menu principal (primeira interação)."""
    return (
        "Olá, bom dia! Sou o Gestoor360, seu assistente de transporte escolar. "
        "Selecione o número da opção desejada:\n\n"
        "1 - Rotas do transporte\n"
        "2 - Horários do transporte\n"
        "3 - Mudar de rota (Ação)\n"
        "4 - Solicitar transporte escolar (Ação)\n"
        "5 - Denúncias (Ação)\n"
        "6 - Outros assuntos"
    )

def consultar_rotas_texto():
    """1- Rotas: Retorna o resumo de todas as rotas ativas."""
    # Nota: Usamos joinedload para buscar os dados do motorista em uma consulta
    rotas = RotaTransporte.query.options(
        joinedload(RotaTransporte.motorista)
    ).all()
    
    if not rotas:
        return "Nenhuma rota de transporte escolar está cadastrada ou ativa no momento."
        
    resposta_texto = "ROTAS CADASTRADAS (DIGITE APENAS O ID, EX: 3, PARA VER DETALHES):\n\n"
    
    for rota in rotas:
        motorista_nome = rota.motorista.nome if rota.motorista else "N/A"
        veiculo_placa = rota.veiculo_placa
        
        # Resumo das escolas atendidas na rota
        escolas_manha = rota.escolas_manha or "N/A"
        
        resposta_texto += (
            f"ID #{rota.id} | Motorista: {motorista_nome}\n"
            f"   Escolas: {escolas_manha}...\n"
            "--------------------\n"
        )
        
    return resposta_texto

def consultar_horarios_texto(rota_id):
    """2- Horários: Busca horários específicos para uma Rota ID."""
    try:
        rota = RotaTransporte.query.get(rota_id)
    except Exception:
        return "Erro interno ao buscar dados. Tente novamente."
        
    if not rota:
        return f"Rota ID {rota_id} não encontrada. Por favor, verifique o número."

    # Formatação dos horários (se existirem)
    saida_manha = rota.horario_saida_manha.strftime('%H:%M') if rota.horario_saida_manha else "--:--"
    volta_manha = rota.horario_volta_manha.strftime('%H:%M') if rota.horario_volta_manha else "--:--"
    saida_tarde = rota.horario_saida_tarde.strftime('%H:%M') if rota.horario_saida_tarde else "--:--"
    volta_tarde = rota.horario_volta_tarde.strftime('%H:%M') if rota.horario_volta_tarde else "--:--"
    
    return (
        f"DETALHES DA ROTA #{rota_id}:\n"
        f"Motorista: {rota.motorista.nome if rota.motorista else 'N/A'}\n"
        f"Veículo: {rota.veiculo_placa}\n\n"
        f"Manhã:\n"
        f"  Saída: {saida_manha}\n"
        f"  Volta: {volta_manha}\n\n"
        f"Tarde:\n"
        f"  Saída: {saida_tarde}\n"
        f"  Volta: {volta_tarde}\n"
    )

def roteador_intencoes(mensagem_original):
    """Decide qual função de consulta chamar."""
    msg = mensagem_original.lower().strip()
    
    # Roteamento baseado em números e palavras-chave
    
    if msg.startswith("1") or "rotas" in msg:
        return consultar_rotas_texto()
        
    if msg.startswith("2") or "horarios" in msg or "horário" in msg:
        return "Para consultar horários específicos, digite APENAS o ID da rota que você viu na Opção 1 (Ex: 3 ou 10)."
        
    # Tenta identificar um número após a Opção 2
    if msg.isdigit():
        rota_id = int(msg)
        return consultar_horarios_texto(rota_id)
        
    # Opções de Ação (Apenas informação textual)
    if msg.startswith("3") or "mudar" in msg:
        return "Para solicitar MUDANÇA DE ROTA, acesse o portal do aluno no site da prefeitura. Esta ação requer login."
        
    if msg.startswith("4") or "solicitar" in msg:
        return "Para SOLICITAR TRANSPORTE ESCOLAR, preencha o cadastro online no site da SEMED."
        
    if msg.startswith("5") or "denuncia" in msg:
        return "Para DENÚNCIAS sobre o transporte, envie um e-mail para ouvidoria@prefeitura.gov.br ou ligue para (00) 9999-9999."

    if msg.startswith("6") or "outros" in msg:
        return "Para outros assuntos, por favor, ligue para a Secretaria de Educação no telefone (00) 3333-4444."
            
    # Resposta Padrão
    return menu_principal()