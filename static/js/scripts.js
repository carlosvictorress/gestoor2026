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

function buscarServidorSuporte() {
    // Agora buscamos exatamente pelo ID que você definiu no HTML
    const inputCpf = document.getElementById('cpf_busca');
    const cpf = inputCpf ? inputCpf.value : "";
    
    if (!cpf || cpf.length < 11) {
        alert("Por favor, informe um CPF válido.");
        return;
    }

    // O restante do fetch continua igual...
    fetch(`/api/buscar-servidor/${cpf}`)
// ...

    // Faz a chamada para a API do seu sistema
    fetch(`/api/buscar-servidor/${cpf}`)
        .then(response => {
            if (!response.ok) throw new Error('Servidor não encontrado');
            return response.json();
        })
        .then(data => {
            // Preenche os dados de Valença do Piauí no seu Modal
            document.getElementById('nome_servidor_modal').innerText = data.nome;
            document.getElementById('escola_servidor_modal').innerText = data.escola;
            
            // Preenche campos escondidos (hidden) para enviar no formulário depois
            document.getElementById('cpf_hidden').value = cpf;
            document.getElementById('escola_id_hidden').value = data.id_escola;

            // Abre o Modal do Bootstrap
            var myModal = new bootstrap.Modal(document.getElementById('modalChamado'));
            myModal.show();
        })
        .catch(error => {
            alert("CPF não localizado na base da Secretaria de Educação.");
            console.error("Erro na busca:", error);
        });
}