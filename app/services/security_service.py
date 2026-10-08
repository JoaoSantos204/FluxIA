import os
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


def exigir_perfil(request, empresa_id: int | None, perfis: list[str]) -> dict:
    """
    Autorização Central do FluxIA:
    1. Lê o cabeçalho X-User-Id da requisição.
    2. Carrega o usuário do PostgreSQL.
    3. Valida que o usuário está ativo.
    4. Valida multi-tenant: pertence à empresa informada (usuário master é exceção global).
    5. Valida perfil: se está na lista de perfis permitidos (ou se é master).
    Retorna o dicionário do usuário autenticado ou lança 401/403.
    """
    user_id_header = None
    if request:
        user_id_header = request.headers.get("X-User-Id") or request.headers.get("X-Usuario-Id")

    if not user_id_header or not str(user_id_header).strip().isdigit():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação necessária. Cabeçalho X-User-Id não informado ou inválido."
        )

    usuario_id = int(str(user_id_header).strip())
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

    # Verificação de tenant
    if empresa_id is not None and usuario.get("empresa_id") != empresa_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado. Você não pertence a esta empresa."
        )

    # Verificação de perfil na matriz permitida
    perfis_normalizados = [p.strip().lower() for p in perfis]
    if perfil not in perfis_normalizados:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Acesso negado. Seu perfil '{perfil}' não tem permissão para realizar esta operação."
        )

    return dict(usuario)

