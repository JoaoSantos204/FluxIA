# Fluxo de Git e Deploy no FluxIA

1. **Ao finalizar qualquer correção ou tarefa**:
   - Sempre pergunte ao usuário se ele deseja commitar as alterações para o GitHub e realizar o deploy no Render.

2. **Ao realizar o deploy**:
   - O auto-deploy automático via webhook no Render não está executando de forma autônoma.
   - Portanto, após o `git push origin main`, SEMPRE execute o deploy via API do Render (`POST https://api.render.com/v1/services/srv-daq2sus9v7es73bj3220/deploys`).
   - Acompanhe o status do deploy (`build_in_progress` -> `live`) e reporte o resultado ao usuário com a URL da aplicação.
