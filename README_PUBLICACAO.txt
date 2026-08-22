BUSCADOR DE PASSAGENS — V10 WEB

Objetivo
-------
Publicar o aplicativo no Streamlit Community Cloud com um link fixo e senha.

Arquivos que podem ir para o GitHub
-----------------------------------
app.py
requirements.txt
.gitignore
.streamlit/config.toml
README_PUBLICACAO.txt

NÃO publique chaves ou senhas.
O arquivo SECRETS_MODELO.toml é apenas um modelo. Não coloque nele sua chave real
antes de enviar ao GitHub.

Secrets a configurar no Streamlit Cloud
----------------------------------------
SERPAPI_API_KEY = "sua chave da SerpApi"
APP_PASSWORD = "sua senha privada"

Opcional:
LATAM_BALANCE = "2284"
SMILES_BALANCE = "51100"
AZUL_BALANCE = "0"

Observação sobre saldos
-----------------------
No Streamlit Cloud, os saldos alterados pela tela ficam na sessão atual.
Os valores iniciais podem ser definidos nos Secrets acima.

Publicação
----------
1. Criar um repositório privado no GitHub.
2. Enviar os arquivos desta pasta ao repositório.
3. Entrar em https://share.streamlit.io/
4. Criar novo app usando esse repositório.
5. Main file path: app.py
6. Em App settings > Secrets, colar SERPAPI_API_KEY e APP_PASSWORD.
7. Fazer o deploy.
8. O Streamlit fornecerá um link fixo terminado em .streamlit.app.
