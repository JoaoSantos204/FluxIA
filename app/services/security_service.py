import os
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)


def verificar_admin_api_key(api_key: str = Security(api_key_header)):
    """Valida a chave administrativa para operações sensíveis."""
    chave_esperada = os.getenv("ADMIN_API_KEY", "fluxia-admin-secret-key-2026")
    if not api_key or api_key != chave_esperada:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso não autorizado. Chave administrativa (X-Admin-API-Key) inválida ou ausente."
        )

