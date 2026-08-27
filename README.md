<div align="center">

# Knox File Host

**Discord Bot • Python • Dev Cloud / File Host API**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Discord](https://img.shields.io/badge/Discord-Slash%20Commands-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.com)
[![API](https://img.shields.io/badge/API-Dev%20Cloud-111827?style=for-the-badge)](https://dev-cloud.base44.app/docs)

**Criado e mantido por [Knox Dev](https://github.com/knox-devx)**

</div>

---

## Visão geral

O bot hospeda anexos do Discord usando a File Host API da **Dev Cloud** e entrega o resultado em dois lugares:

1. por **DM**;
2. no canal onde `/hospedar` foi usado como resposta **ephemeral**, visível somente para quem executou o comando.

Ele suporta arquivos públicos, privados, senha e links temporários assinados.

## Fluxo de links grandes

O Discord limita URLs de botões a **512 caracteres**. Signed URLs privadas podem ultrapassar esse limite.

O bot resolve isso automaticamente:

1. gera o `signed_url` normalmente;
2. se ele couber em 512 caracteres, cria o botão **Abrir arquivo** diretamente;
3. se passar de 512, chama `POST /functions/shortenUrl` da **Dev Cloud**;
4. se o link encurtado couber, usa o link curto no botão;
5. se o encurtador estiver indisponível, ainda não tiver sido publicado ou retornar um link grande demais, o bot mostra o botão **Receber link**;
6. ao clicar em **Receber link**, somente o dono do upload recebe o link completo e o bot também tenta enviar uma cópia por DM.

Assim o upload não falha por causa de `400 Invalid Form Body` do Discord.

## Endpoints usados

```text
POST https://dev-cloud.base44.app/functions/uploadFile
POST https://dev-cloud.base44.app/functions/createSignedUrl
POST https://dev-cloud.base44.app/functions/shortenUrl
```

### uploadFile

`multipart/form-data`:

```text
file=<arquivo>
private=true|false
password=<opcional>
```

### createSignedUrl

```json
{
  "file_uri": "FILE_URI",
  "expires_in": 3600,
  "password": "SENHA_SE_EXISTIR"
}
```

### shortenUrl

O bot espera esta interface da Dev Cloud:

```json
{
  "url": "https://link-original-muito-grande...",
  "expires_in": 3600
}
```

Resposta recomendada:

```json
{
  "short_url": "https://dev-cloud.base44.app/s/AbC123",
  "code": "AbC123",
  "expires_at": "2026-08-27T05:00:00Z"
}
```

O bot também reconhece `shortUrl`, `url` ou `link` como aliases de `short_url`.

## Comando

```text
/hospedar arquivo:<anexo> privado:<true|false> senha:<opcional> expira_em:<60..2592000>
```

Exemplo:

```text
/hospedar arquivo:backup.zip privado:true senha:minha-senha expira_em:3600
```

## Configuração

```env
DISCORD_TOKEN=TOKEN_DO_BOT
BOT_NAME=Knox File Host
SYNC_COMMANDS=true

API_FUNCTIONS_URL=https://dev-cloud.base44.app/functions
API_UPLOAD_URL=https://dev-cloud.base44.app/functions/uploadFile
API_SIGNED_URL=https://dev-cloud.base44.app/functions/createSignedUrl
API_SHORTEN_URL=https://dev-cloud.base44.app/functions/shortenUrl

API_CONNECT_TIMEOUT=30
API_READ_TIMEOUT=1800
```

## Instalação

```bash
git clone https://github.com/knox-devx/bot-discord-file-host.git
cd bot-discord-file-host
pip install -r requirements.txt
python3 main.py
```

## Wispbyte

Startup recomendado quando as dependências ainda precisam ser instaladas:

```bash
python3 -m pip install --user --no-cache-dir -r requirements.txt && python3 main.py
```

Depois que as dependências já estiverem presentes:

```bash
python3 main.py
```

## Segurança

- a senha não é exibida nas respostas;
- o link completo de fallback só é entregue ao usuário que iniciou o upload;
- respostas no servidor são ephemeral;
- arquivos temporários locais são removidos após processamento;
- se a DM estiver bloqueada, o upload continua funcionando.

---

<div align="center">

**Knox Dev • File Hosting • Dev Cloud**

</div>
