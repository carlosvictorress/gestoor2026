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
    const inputCpf = document.getElementById('cpf_busca');
    let cpf = inputCpf ? inputCpf.value : "";
    
    // Limpa o CPF de pontos e traços no lado do cliente também
    cpf = cpf.replace(/\D/g, '');

    if (!cpf || cpf.length < 11) {
        alert("Por favor, informe um CPF válido com 11 dígitos.");
        return;
    }

    // Chamada para a rota definida no helpdesk_routes.py
    fetch(`/api/buscar-servidor/${cpf}`)
        .then(response => {
            if (!response.ok) {
                if(response.status === 404) throw new Error('CPF não localizado');
                throw new Error('Erro no servidor');
            }
            return response.json();
        })
        .then(data => {
            // Preenche os IDs exatos que estão no seu suporte_externo.html
            document.getElementById('nome_servidor').innerText = data.nome;
            document.getElementById('escola_servidor').innerText = data.escola;
            document.getElementById('hidden_cpf').value = cpf;
            document.getElementById('hidden_escola').value = data.id_escola;

            // Abre o Modal (use o ID modalHelpDesk que está no seu HTML)
            var myModal = new bootstrap.Modal(document.getElementById('modalHelpDesk'));
            myModal.show();
        })
        .catch(error => {
            alert(error.message === 'CPF não localizado' 
                ? "CPF não localizado na base da Secretaria de Educação de Valença." 
                : "Erro ao conectar com o sistema.");
            console.error(error);
        });
}