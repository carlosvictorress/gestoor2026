from flask import Blueprint, render_template, request, flash, session
from extensions import db
from sqlalchemy import text
from utils import login_required, role_required, registrar_log

# Definição do Blueprint
admin_sql_bp = Blueprint('admin_sql', __name__, url_prefix='/admin/database')

@admin_sql_bp.route('/editor', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def sql_editor():
    query_result = None
    error = None
    sql_query = request.form.get('sql_query', '')

    if request.method == 'POST' and sql_query:
        try:
            # Proteção simples contra comandos perigosos
            if any(cmd in sql_query.upper() for cmd in ['DROP', 'TRUNCATE']):
                if not session.get('confirm_danger_sql'):
                    flash("Comando estrutural detectado! Clique em 'Executar' novamente para confirmar.", "warning")
                    session['confirm_danger_sql'] = True
                    return render_template('admin/sql_editor.html', sql_query=sql_query)
            
            # Execução da Query via SQLAlchemy
            result = db.session.execute(text(sql_query))
            
            if sql_query.strip().upper().startswith('SELECT'):
                query_result = {
                    'columns': result.keys(),
                    'rows': result.fetchall()
                }
            else:
                db.session.commit()
                registrar_log(f"Executou comando SQL manual: {sql_query[:100]}")
                flash("Comando executado com sucesso!", "success")
            
            session.pop('confirm_danger_sql', None)

        except Exception as e:
            db.session.rollback()
            error = str(e)

    return render_template('admin/sql_editor.html', 
                           query_result=query_result, 
                           error=error, 
                           sql_query=sql_query)