from datetime import datetime
from app.database.database import conectar

def inserir_usuario():
    conn = conectar()
    cursor = conn.cursor()
    
    # Gera a data e hora atual no formato padrão do SQLite
    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        cursor.execute("""
            INSERT INTO usuarios (nome, email, empresa_id, senha_hash, data_criacao)
            VALUES (?, ?, ?, ?, ?)
        """, ("Joao", "joao2012vr@gmail.com", 1, "hash_senha_teste_123", data_atual))
        
        conn.commit()
        print("✅ Usuário inserido com sucesso!")
    except Exception as e:
        print(f"❌ Erro ao inserir: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    inserir_usuario()