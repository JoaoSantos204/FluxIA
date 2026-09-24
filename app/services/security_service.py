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
