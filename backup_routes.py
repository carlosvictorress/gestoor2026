import os
import shutil
import zipfile
from datetime import datetime
from flask import Blueprint, render_template, current_app, flash, redirect, url_for, send_from_directory, request
from utils import login_required, admin_required
from werkzeug.utils import secure_filename

backup_bp = Blueprint('backup', __name__)

# Define o caminho para a pasta de backups
BACKUP_FOLDER_NAME = 'backups'

def get_backup_folder():
    """Retorna o caminho completo da pasta de backups e a cria se não existir."""
    path = os.path.join(current_app.root_path, '..', BACKUP_FOLDER_NAME)
    os.makedirs(path, exist_ok=True)
    return path

@backup_bp.route('/administracao/backup')
@login_required
@admin_required
def index():
    """Exibe a página de backup com a lista de backups existentes."""
    backup_folder = get_backup_folder()
    try:
        backups_existentes = sorted(
            [f for f in os.listdir(backup_folder) if f.endswith('.zip')],
            reverse=True
        )
    except OSError:
        backups_existentes = []
        flash("Não foi possível acessar a pasta de backups.", "danger")
        
    return render_template('backup.html', backups=backups_existentes)

@backup_bp.route('/administracao/backup/gerar')
@login_required
@admin_required
def gerar_backup():
    """Cria um backup completo do banco de dados e dos arquivos de upload."""
    try:
        # 1. Preparar nomes e pastas
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        backup_filename_base = f'backup_syseduca_{timestamp}'
        backup_folder = get_backup_folder()
        temp_backup_dir = os.path.join(backup_folder, backup_filename_base)
        os.makedirs(temp_backup_dir, exist_ok=True)

        # 2. Fazer backup do banco de dados (copia simples do arquivo)
        db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        db_backup_path = os.path.join(temp_backup_dir, 'servidores.db')
        shutil.copy2(db_path, db_backup_path)

        # 3. Fazer backup da pasta de uploads (compactando em um zip)
        uploads_folder = current_app.config['UPLOAD_FOLDER']
        uploads_zip_path = os.path.join(temp_backup_dir, 'uploads.zip')
        with zipfile.ZipFile(uploads_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(uploads_folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Adiciona o arquivo ao zip, mantendo a estrutura de pastas relativas
                    zipf.write(file_path, os.path.relpath(file_path, uploads_folder))

        # 4. Compactar a pasta temporária em um único arquivo .zip final
        final_zip_path = os.path.join(backup_folder, f'{backup_filename_base}.zip')
        shutil.make_archive(os.path.join(backup_folder, backup_filename_base), 'zip', temp_backup_dir)

        # 5. Limpar a pasta temporária
        shutil.rmtree(temp_backup_dir)

        flash(f'Backup "{backup_filename_base}.zip" gerado com sucesso!', 'success')
    except Exception as e:
        flash(f'Ocorreu um erro ao gerar o backup: {e}', 'danger')

    return redirect(url_for('backup.index'))


@backup_bp.route('/administracao/backup/download/<filename>')
@login_required
@admin_required
def download_backup(filename):
    """Permite o download de um arquivo de backup existente."""
    backup_folder = get_backup_folder()
    return send_from_directory(backup_folder, filename, as_attachment=True)
    
    
    
@backup_bp.route('/administracao/backup/upload', methods=['POST'])
@login_required
@admin_required
def upload_backup():
    """Recebe um arquivo de backup via upload."""
    if 'backup_file' not in request.files:
        flash('Nenhum arquivo enviado.', 'warning')
        return redirect(url_for('backup.index'))

    file = request.files['backup_file']
    if file.filename == '' or not file.filename.endswith('.zip'):
        flash('Nenhum arquivo selecionado ou formato inválido (deve ser .zip).', 'warning')
        return redirect(url_for('backup.index'))

    try:
        filename = secure_filename(file.filename)
        backup_folder = get_backup_folder()
        file.save(os.path.join(backup_folder, filename))
        flash(f'Arquivo "{filename}" enviado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao salvar o arquivo enviado: {e}', 'danger')

    return redirect(url_for('backup.index'))


@backup_bp.route('/administracao/backup/restaurar/<filename>')
@login_required
@admin_required
def restaurar_backup(filename):
    """Exibe uma tela de confirmação antes de restaurar."""
    return render_template('restaurar_confirmacao.html', filename=filename)


@backup_bp.route('/administracao/backup/executar_restauracao', methods=['POST'])
@login_required
@admin_required
def executar_restauracao():
    """Prepara os arquivos de um backup para a restauração manual."""
    filename = request.form.get('filename')
    if not filename:
        flash('Nome do arquivo de backup inválido.', 'danger')
        return redirect(url_for('backup.index'))

    backup_folder = get_backup_folder()
    file_path = os.path.join(backup_folder, filename)
    restore_temp_dir = os.path.join(backup_folder, 'restore_temp')

    try:
        # Limpa a pasta de restauração anterior, se existir
        if os.path.exists(restore_temp_dir):
            shutil.rmtree(restore_temp_dir)
        os.makedirs(restore_temp_dir, exist_ok=True)

        # 1. Descompacta o backup principal na pasta temporária
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(restore_temp_dir)

        # 2. Descompacta o 'uploads.zip' que estava dentro do backup
        uploads_zip_path = os.path.join(restore_temp_dir, 'uploads.zip')
        if os.path.exists(uploads_zip_path):
            with zipfile.ZipFile(uploads_zip_path, 'r') as zip_ref:
                zip_ref.extractall(os.path.join(restore_temp_dir, 'uploads'))
            os.remove(uploads_zip_path) # Remove o .zip aninhado

        # Mensagem final com as instruções manuais
        flash_message = """
        <strong>Arquivos de restauração preparados!</strong><br>
        Para concluir, siga <strong>EXATAMENTE</strong> os passos abaixo:
        <ol class='mt-2'>
            <li><strong>Pare a aplicação Flask</strong> (pressione CTRL + C no terminal).</li>
            <li>Na pasta do projeto, <strong>apague completamente a pasta 'uploads'</strong>.</li>
            <li><strong>Apague o arquivo de banco de dados</strong> atual: <code>servidores.db</code>.</li>
            <li>Vá para a pasta <code>backups/restore_temp/</code>.</li>
            <li><strong>Copie</strong> o arquivo <code>servidores.db</code> e a pasta <code>uploads</code> de dentro da <code>restore_temp</code> para a <strong>pasta principal do seu projeto</strong>.</li>
            <li><strong>Inicie a aplicação Flask novamente</strong> (<code>flask run --host=0.0.0.0</code>).</li>
        </ol>
        Seus dados terão sido restaurados.
        """
        flash(flash_message, 'info')

    except Exception as e:
        flash(f'Ocorreu um erro ao preparar a restauração: {e}', 'danger')

    return redirect(url_for('backup.index'))    
