# Organizador de Drive — Sara Rocha Advocacia

Script Python que migra automaticamente clientes do Google Drive de uma estrutura desorganizada para uma estrutura organizada por categorias juridicas.

## O que faz

Para cada cliente na pasta antiga, o script:
1. Localiza todos os PDFs
2. Classifica cada pagina por tipo de documento (usando palavras-chave juridicas em portugues)
3. Cria a nova estrutura de pastas no Drive
4. Faz upload dos PDFs separados nas pastas corretas

### Estrutura gerada

```
01_CLIENTES/
  NOME DO CLIENTE/
    01_DOCUMENTOS/          <- RG, contas, laudos medicos
    02_PROCESSO JUDICIAL/   <- Procuracao, declaracoes, pericia
    03_ADMINISTRATIVO/      <- PPP, CNIS, INSS, formularios
    04_CONTRATO/            <- Contrato de prestacao de servicos
```

## Categorias identificadas automaticamente

| Pasta | Documentos |
|-------|-----------|
| 01_DOCUMENTOS | RG, carteira de identidade, laudos medicos, contas |
| 02_PROCESSO JUDICIAL | Procuracao, declaracao de pobreza, pericia, tutela |
| 03_ADMINISTRATIVO | PPP, CNIS, INSS, extrato previdenciario, beneficio |
| 04_CONTRATO | Contrato de prestacao de servicos advocaticios |

## Instalacao

```bash
pip install PyMuPDF google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

## Configuracao

1. Acesse o [Google Cloud Console](https://console.cloud.google.com/)
2. Crie um projeto e ative a **Google Drive API**
3. Crie credenciais OAuth 2.0 (Tipo: App para computador)
4. Baixe o arquivo e renomeie para `credentials.json`
5. Coloque o `credentials.json` na mesma pasta do script

## Uso

```bash
python organizador_drive.py
```

Na primeira execucao, um navegador abrira para autorizar o acesso ao Drive.
O progresso e salvo em `progresso.json` - se interrompido, e so rodar novamente.

## Arquivos

| Arquivo | Descricao |
|---------|-----------|
| `organizador_drive.py` | Script principal |
| `credentials.json` | Suas credenciais (nao incluso - ver configuracao) |
| `token.json` | Gerado automaticamente na 1a execucao |
| `progresso.json` | Registro de progresso (gerado automaticamente) |
| `organizador.log` | Log detalhado de execucao (gerado automaticamente) |

## Observacoes

- A logica de classificacao e baseada na mesma usada no site [docsararocha.netlify.app](https://docsararocha.netlify.app/)
- Documentos escaneados sem texto embutido sao classificados como "Documentos" por padrao
- O script suporta retomada: clientes ja processados sao ignorados nas execucoes seguintes

## Seguranca

**Nunca suba o `credentials.json` ou `token.json` para o GitHub.** Esses arquivos dao acesso total ao seu Google Drive. Eles estao no `.gitignore` por padrao.
