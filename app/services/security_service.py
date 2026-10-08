import os
from typing import Optional
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader
from app.database.database import conectar, _cursor, _placeholder

api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)


def verificar_admin_api_key(api_key: str = Security(api_key_header)):
    """Valida a chave administrativa para operações do sistema."""
    chave_esperada = os.getenv("ADMIN_API_KEY", "fluxia-admin-secret-key-2026")
    if not api_key or api_key != chave_esperada:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso não autorizado. Chave administrativa (X-Admin-API-Key) inválida ou ausente."
        )


def validar_perfil_admin_ou_master(usuario_id: int) -> dict:
    """
    Validação RBAC Real:
    Verifica se o usuário existe no banco e se possui perfil 'admin' ou 'master'.
    Bloqueia 'funcionario' e 'cliente' com HTTP 403 Forbidden.
    """
    if not usuario_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identificação do usuário (usuario_id) é obrigatória para esta operação."
        )

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            SELECT id, nome, email, perfil, status, empresa_id
            FROM usuarios
            WHERE id = {ph}
        """, (usuario_id,))
        usuario = cursor.fetchone()
    finally:
        conexao.close()

    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Usuário ID {usuario_id} não encontrado no sistema."
        )

    if usuario.get("status") != "ativo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário inativo. Operação negada."
        )

    perfil = str(usuario.get("perfil", "cliente")).lower()
    if perfil not in ("admin", "master"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Acesso negado. Apenas perfis 'admin' ou 'master' podem gerenciar documentos. Seu perfil atual é '{perfil}'."
        )

    return dict(usuario)


def exigir_perfil(request=None, arg2=None, arg3=None, empresa_id: Optional[int] = None, perfis: Optional[list] = None) -> dict:
    """
    Autorização Central do FluxIA:
    Aceita exigir_perfil(request, empresa_id, perfis) ou exigir_perfil(request, perfis, empresa_id=...)
    1. Lê o cabeçalho X-User-Id / X-Usuario-Id ou query param usuario_id da requisição.
    2. Carrega o usuário do PostgreSQL.
    3. Valida que o usuário está ativo.
    4. Valida perfil: se está na lista de perfis permitidos (ou se é master).
    5. Valida multi-tenant:
       - Master tem acesso a qualquer empresa.
       - Usuários comuns (admin/funcionario) operam estritamente em sua empresa (usuario["empresa_id"]).
         Se tentarem acessar explicitamente outra empresa via query param empresa_id diferente, lança 403.
    Retorna o dicionário do usuário autenticado ou lança 401/403.
    """
    if isinstance(arg2, (list, tuple, set)):
        perfis = list(arg2)
        if isinstance(arg3, (int, str)) and str(arg3).isdigit():
            empresa_id = int(arg3)
    elif isinstance(arg3, (list, tuple, set)):
        perfis = list(arg3)
        if isinstance(arg2, (int, str)) and str(arg2).isdigit():
            empresa_id = int(arg2)
    elif arg2 is not None and (isinstance(arg2, int) or str(arg2).isdigit()):
        empresa_id = int(arg2)

    if not perfis:
        perfis = ["admin", "funcionario", "master"]
    user_id_val = None
    if request:
        user_id_val = (
            request.headers.get("X-User-Id")
            or request.headers.get("X-Usuario-Id")
            or request.query_params.get("usuario_id")
            or request.query_params.get("user_id")
        )

    if not user_id_val or not str(user_id_val).strip().isdigit():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação necessária. Cabeçalho X-User-Id não informado ou inválido."
        )

    usuario_id = int(str(user_id_val).strip())
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            SELECT id, empresa_id, nome, email, perfil, status
            FROM usuarios
            WHERE id = {ph}
        """, (usuario_id,))
        usuario = cursor.fetchone()
    finally:
        conexao.close()

    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Usuário ID {usuario_id} não encontrado."
        )

    if usuario.get("status") != "ativo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário inativo. Operação negada."
        )

    perfil = str(usuario.get("perfil") or "cliente").strip().lower()

    # Usuário master tem acesso irrestrito
    if perfil == "master":
        return dict(usuario)

    # Verificação de perfil na matriz permitida
    perfis_normalizados = [p.strip().lower() for p in perfis]
    if perfil not in perfis_normalizados:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Acesso negado. Seu perfil '{perfil}' não tem permissão para realizar esta operação."
        )

    # Verificação de tenant para não-master:
    # Se o cliente enviou explicitamente empresa_id na URL e for diferente de sua própria empresa, bloqueia
    if request:
        param_empresa = request.query_params.get("empresa_id")
        if param_empresa and param_empresa.strip().isdigit():
            emp_solicitada = int(param_empresa.strip())
            if emp_solicitada != usuario.get("empresa_id"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Acesso negado. Você não pertence a esta empresa."
                )

    return dict(usuario)

