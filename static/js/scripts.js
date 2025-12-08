document.addEventListener('DOMContentLoaded', function() {
    const modal = document.getElementById('confirmarAcaoModal');
    const btnConfirmar = document.getElementById('btnConfirmarUniversal');
    const modalMensagem = document.getElementById('mensagemConfirmacao');

    // 1. Usa Event Delegation no documento para capturar cliques em links de exclusão
    document.addEventListener('click', function(e) {
        // Encontra o link de exclusão mais próximo que tenha a classe customizada 'js-confirm-delete'
        // OU que ainda tenha o atributo 'onclick' com 'confirm' (para compatibilidade temporária)
        const link = e.target.closest('a.js-confirm-delete');
        
        if (!link) {
            return;
        }

        // --- Intercepta a Ação ---
        e.preventDefault(); // Impede o comportamento padrão (navegação imediata ou onclick)

        // 2. Tenta extrair a URL e a mensagem
        let urlOriginal = link.getAttribute('href');
        let mensagem = 'Tem certeza que deseja prosseguir com esta ação?';

        // Tenta extrair a mensagem do atributo onclick (para migrar os links antigos)
        const onclickAttr = link.getAttribute('onclick');
        if (onclickAttr) {
             const match = onclickAttr.match(/confirm\(['"]([^'"]+)['"]\)/);
             if (match && match[1]) {
                 mensagem = match[1];
             }
        }
        
        // --- Preenche e Exibe o Modal ---
        modalMensagem.textContent = mensagem;
        btnConfirmar.setAttribute('href', urlOriginal);
        
        const modalInstance = new bootstrap.Modal(modal);
        modalInstance.show();
    });
});