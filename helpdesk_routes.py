@helpdesk_bp.route('/painel-chamados')
@login_required
def painel_chamados():
    # Busca chamados abertos e em andamento primeiro
    chamados = ChamadoTecnico.query.order_by(ChamadoTecnico.data_abertura.desc()).all()
    
    # Contadores para o dashboard
    total_abertos = ChamadoTecnico.query.filter_by(status='Aberto').count()
    total_andamento = ChamadoTecnico.query.filter_by(status='Em Andamento').count()
    
    return render_template('painel_chamados.html', chamados=chamados, abertos=total_abertos, andamento=total_andamento)