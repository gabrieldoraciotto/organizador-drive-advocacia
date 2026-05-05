#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Organizador de Drive — Sara Rocha Advocacia
============================================
Migra clientes da pasta antiga "Clientes " para a nova estrutura "01_CLIENTES",
criando 4 subpastas por cliente e separando os PDFs por categoria.

Estrutura gerada:
  01_CLIENTES/
    NOME DO CLIENTE/
      01_DOCUMENTOS/         ← RG, contas, laudos médicos
      02_PROCESSO JUDICIAL/  ← Procuração, declarações, perícia
      03_ADMINISTRATIVO/     ← PPP, CNIS, INSS, formulários
      04_CONTRATO/           ← Contrato de prestação de serviços

Como usar:
  1. Instale as dependências:
       pip install PyMuPDF google-api-python-client google-auth-httplib2 google-auth-oauthlib
  2. Copie o arquivo config.exemplo.py para config.py e preencha os IDs das pastas
  3. Coloque o arquivo credentials.json na mesma pasta deste script
  4. Execute: python organizador_drive.py
  5. Na primeira execução, um navegador abrirá para você autorizar o acesso ao Drive.
"""

import os
import io
import json
import re
import sys
import stat
import logging
import time
import unicodedata
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# VERIFICAÇÃO DE DEPENDÊNCIAS
# ─────────────────────────────────────────────────────────────────────────────
try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERRO: PyMuPDF não instalado. Execute: pip install PyMuPDF")
    sys.exit(1)

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
except ImportError:
    print("ERRO: Google API não instalada. Execute:")
    print("  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO — lida de config.py (nunca suba esse arquivo para o GitHub)
# ─────────────────────────────────────────────────────────────────────────────
try:
    import config
    OLD_CLIENTES_ID = config.OLD_CLIENTES_ID
    NEW_CLIENTES_ID = config.NEW_CLIENTES_ID
except ImportError:
    # Fallback: lê de variáveis de ambiente
    OLD_CLIENTES_ID = os.environ.get("OLD_CLIENTES_ID", "")
    NEW_CLIENTES_ID = os.environ.get("NEW_CLIENTES_ID", "")
    if not OLD_CLIENTES_ID or not NEW_CLIENTES_ID:
        print("ERRO: Crie um arquivo config.py com OLD_CLIENTES_ID e NEW_CLIENTES_ID.")
        print("      Veja o arquivo config.exemplo.py para referência.")
        sys.exit(1)

# [FIX-ALTO] Escopo mínimo necessário: apenas leitura/escrita de arquivos criados pelo app
# Nota: 'drive' dá acesso total a todo o Drive. Usamos 'drive' aqui pois o script
# precisa acessar pastas existentes criadas por outros meios.
# Se possível, migre para 'drive.file' após reorganizar as pastas.
SCOPES           = ['https://www.googleapis.com/auth/drive']

CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE       = 'token.json'
PROGRESS_FILE    = 'progresso.json'
LOG_FILE         = 'organizador.log'

# Limites de segurança (proteção contra DoS/exaustão de memória)
MAX_PDF_SIZE_MB  = 50        # [FIX-MÉDIO] Ignora PDFs maiores que 50 MB
MAX_PAGES        = 500       # [FIX-MÉDIO] Ignora PDFs com mais de 500 páginas

# Regex para validar IDs do Google Drive (apenas alfanuméricos + hífen + underscore)
_DRIVE_ID_RE = re.compile(r'^[A-Za-z0-9_\-]{10,}$')

# Nomes das subpastas
SUBFOLDERS = [
    '01_DOCUMENTOS',
    '02_PROCESSO JUDICIAL',
    '03_ADMINISTRATIVO',
    '04_CONTRATO',
]

# Categoria → subpasta
CAT_TO_FOLDER = {
    'Documentos':                '01_DOCUMENTOS',
    'Documentos Judiciais':      '02_PROCESSO JUDICIAL',
    'Documentos Administrativos':'03_ADMINISTRATIVO',
    'Contrato':                  '04_CONTRATO',
}


# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFICAÇÃO
# ─────────────────────────────────────────────────────────────────────────────
CAT_RULES = [
    {
        'cat': 'Contrato',
        'keywords': [
            'contrato de prestacao', 'servicos advocaticios', 'clausula',
            'contratante', 'contratada', 'honorarios advocaticios',
            'rescisao contratual', 'titulo executivo',
        ]
    },
    {
        'cat': 'Documentos Judiciais',
        'keywords': [
            'procuracao', 'ad judicia', 'outorgante', 'outorgada',
            'declaracao de estado de pobreza', 'justica gratuita',
            'hipossuficiencia', 'aviso de pericia', 'pericia foi marcada',
            'juizado especial federal', 'tutela antecipada',
        ]
    },
    {
        'cat': 'Documentos Administrativos',
        'keywords': [
            'perfil profissiografico', 'ppp', 'cnis',
            'cadastro nacional de informacoes sociais', 'comunicacao de decisao',
            'auxilio doenca', 'extrato previdenciario', 'relacoes previdenciarias',
            'secao de dados administrativos', 'salario de contribuicao',
            'previdencia social', 'instituto nacional do seguro social',
            'numero do beneficio', 'deferimento', 'proevi', 'irecol',
        ]
    },
    {
        'cat': 'Documentos',
        'keywords': [
            'carteira de identidade', 'registro geral', 'naturalidade',
            'cedula de identidade', 'total a pagar', 'vencimento', 'fatura',
            'fornecimento', 'energia eletrica', 'kwh', 'relatorio medico',
            'diagnostico', 'internacoes', 'cirurgia', 'receita medica',
            'cid', 'prontuario', 'medico responsavel', 'hospital', 'clinica',
            'reabilitacao', 'carteira de trabalho', 'ctps', 'polegar',
        ]
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS DE SEGURANÇA
# ─────────────────────────────────────────────────────────────────────────────

def validar_drive_id(folder_id: str) -> str:
    """
    [FIX-CRÍTICO] Valida que um ID do Google Drive tem formato esperado.
    Previne Drive API Query Injection ao garantir que o ID só contém
    caracteres alfanuméricos, hífens e underscores.
    Lança ValueError se o ID for inválido.
    """
    if not folder_id or not isinstance(folder_id, str):
        raise ValueError(f"ID de pasta inválido: {folder_id!r}")
    if not _DRIVE_ID_RE.match(folder_id):
        raise ValueError(
            f"ID de pasta com formato suspeito (possível injection): {folder_id!r}"
        )
    return folder_id


def sanitizar_log(texto: str) -> str:
    """
    [FIX-MÉDIO] Remove caracteres de controle e sequências ANSI do texto
    antes de logar, prevenindo Log Injection.
    """
    if not texto:
        return ""
    # Remove sequências ANSI de cor/controle
    texto = re.sub(r'\x1b\[[0-9;]*[mGKHF]', '', texto)
    # Substitui caracteres de controle (exceto tab) por espaço
    texto = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', ' ', texto)
    # Colapsa newlines para evitar log injection (uma entrada = uma linha)
    texto = texto.replace('\n', ' ').replace('\r', ' ')
    return texto


def escape_drive_query_string(valor: str) -> str:
    """
    [FIX-CRÍTICO] Escapa um valor para uso seguro em queries da Drive API.
    O Drive API usa single-quotes e escapa com backslash.
    Referência: https://developers.google.com/drive/api/guides/search-files
    """
    # Escapa backslash primeiro, depois single-quote
    return valor.replace('\\', '\\\\').replace("'", "\\'")


def _set_arquivo_privado(caminho: str) -> None:
    """
    [FIX-ALTO] Define permissões 600 (só dono pode ler/gravar) em um arquivo.
    Protege credentials e tokens de leitura por outros usuários do sistema.
    """
    try:
        os.chmod(caminho, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except OSError as e:
        logging.warning(f"Não foi possível restringir permissões de {caminho}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFICAÇÃO E PROCESSAMENTO DE PDF
# ─────────────────────────────────────────────────────────────────────────────

def normalizar(texto):
    """Remove acentos e normaliza o texto."""
    if not texto:
        return ''
    texto = texto.lower()
    nfkd = unicodedata.normalize('NFD', texto)
    texto = ''.join(c for c in nfkd if not unicodedata.combining(c))
    for errado, certo in [('6', 'o'), ('0', 'o'), ('8', 'e'), ('1', 'l')]:
        texto = texto.replace(errado, certo)
    texto = re.sub(r'[^a-z\s]', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto


def classificar_pagina(texto):
    """Classifica o texto de uma página em uma das 4 categorias."""
    t = normalizar(texto)
    for regra in CAT_RULES:
        if any(kw in t for kw in regra['keywords']):
            return regra['cat']
    return 'Documentos'


def separar_pdf_por_categoria(pdf_bytes, nome_arquivo=""):
    """
    Recebe bytes de um PDF, classifica cada página e retorna
    um dicionário {categoria: bytes_do_pdf} para categorias não-vazias.
    [FIX-MÉDIO] Verifica limites de tamanho e número de páginas antes de processar.
    """
    # Verifica tamanho em memória
    tamanho_mb = len(pdf_bytes) / (1024 * 1024)
    if tamanho_mb > MAX_PDF_SIZE_MB:
        raise ValueError(
            f"PDF muito grande ({tamanho_mb:.1f} MB > {MAX_PDF_SIZE_MB} MB). "
            f"Arquivo ignorado por segurança."
        )

    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    num_pages = len(doc)

    # [FIX-MÉDIO] Verifica número de páginas
    if num_pages > MAX_PAGES:
        doc.close()
        raise ValueError(
            f"PDF com muitas páginas ({num_pages} > {MAX_PAGES}). "
            f"Arquivo ignorado por segurança."
        )

    categorias_por_pagina = []
    for i in range(num_pages):
        page = doc[i]
        texto = page.get_text().strip()
        cat = classificar_pagina(texto)
        categorias_por_pagina.append(cat)
        logging.info(f"    Pág {i+1:3d}: {cat}")

    resultado = {}
    for cat in set(categorias_por_pagina):
        novo_doc = fitz.open()
        for i, pg_cat in enumerate(categorias_por_pagina):
            if pg_cat == cat:
                novo_doc.insert_pdf(doc, from_page=i, to_page=i)
        if len(novo_doc) > 0:
            buf = io.BytesIO()
            novo_doc.save(buf)
            resultado[cat] = buf.getvalue()
        novo_doc.close()

    doc.close()
    return resultado


# ─────────────────────────────────────────────────────────────────────────────
# GOOGLE DRIVE — autenticação e operações
# ─────────────────────────────────────────────────────────────────────────────

def autenticar():
    """Autentica com a API do Google Drive e retorna o serviço."""
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"\nERRO: Arquivo '{CREDENTIALS_FILE}' não encontrado.")
        sys.exit(1)

    # [FIX-ALTO] Protege o arquivo de credenciais logo na inicialização
    _set_arquivo_privado(CREDENTIALS_FILE)

    creds = None
    if os.path.exists(TOKEN_FILE):
        # [FIX-ALTO] Garante permissão restrita antes de ler
        _set_arquivo_privado(TOKEN_FILE)
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # [FIX-ALTO] Escreve token.json e imediatamente restringe permissões
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
        _set_arquivo_privado(TOKEN_FILE)

    return build('drive', 'v3', credentials=creds)


def listar_subpastas(service, parent_id):
    """
    Lista todas as subpastas de uma pasta.
    [FIX-CRÍTICO] Valida o parent_id antes de usá-lo na query.
    """
    validar_drive_id(parent_id)  # Previne Query Injection
    pastas = []
    token = None
    while True:
        resp = service.files().list(
            q=f"mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false",
            fields='nextPageToken, files(id, name)',
            pageSize=1000,
            pageToken=token
        ).execute()
        pastas.extend(resp.get('files', []))
        token = resp.get('nextPageToken')
        if not token:
            break
    return pastas


def listar_pdfs(service, parent_id):
    """
    Lista todos os arquivos PDF em uma pasta.
    [FIX-CRÍTICO] Valida o parent_id antes de usá-lo na query.
    """
    validar_drive_id(parent_id)  # Previne Query Injection
    arquivos = []
    token = None
    while True:
        resp = service.files().list(
            q=f"mimeType='application/pdf' and '{parent_id}' in parents and trashed=false",
            fields='nextPageToken, files(id, name, size)',
            pageSize=1000,
            pageToken=token
        ).execute()
        arquivos.extend(resp.get('files', []))
        token = resp.get('nextPageToken')
        if not token:
            break
    return arquivos


def baixar_arquivo(service, file_id, tamanho_bytes=None):
    """
    Baixa um arquivo do Drive e retorna seus bytes.
    [FIX-MÉDIO] Verifica tamanho antes de baixar para evitar exaustão de memória.
    [FIX-CRÍTICO] Valida o file_id.
    """
    validar_drive_id(file_id)  # Previne possível injection

    # Verifica tamanho antes de baixar (se disponível nos metadados)
    if tamanho_bytes is not None:
        tamanho_mb = int(tamanho_bytes) / (1024 * 1024)
        if tamanho_mb > MAX_PDF_SIZE_MB:
            raise ValueError(
                f"Arquivo muito grande ({tamanho_mb:.1f} MB > {MAX_PDF_SIZE_MB} MB). "
                f"Download cancelado."
            )

    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request, chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def criar_pasta(service, nome, parent_id):
    """Cria uma subpasta no Drive e retorna seu ID."""
    validar_drive_id(parent_id)
    metadata = {
        'name': nome,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_id]
    }
    pasta = service.files().create(body=metadata, fields='id').execute()
    return pasta['id']


def obter_ou_criar_pasta(service, nome, parent_id, cache=None):
    """
    Retorna o ID de uma pasta (existente ou criada) dentro de parent_id.
    [FIX-CRÍTICO] Usa escape_drive_query_string() para evitar Query Injection
    via nome de cliente malicioso.
    """
    validar_drive_id(parent_id)

    if cache and nome in cache:
        return cache[nome]

    # [FIX-CRÍTICO] Escapa o nome corretamente para a query da Drive API
    nome_escaped = escape_drive_query_string(nome)

    resp = service.files().list(
        q=(
            f"mimeType='application/vnd.google-apps.folder' "
            f"and '{parent_id}' in parents "
            f"and name='{nome_escaped}' "
            f"and trashed=false"
        ),
        fields='files(id, name)',
        pageSize=10
    ).execute()

    arquivos = resp.get('files', [])
    if arquivos:
        folder_id = arquivos[0]['id']
    else:
        folder_id = criar_pasta(service, nome, parent_id)

    if cache is not None:
        cache[nome] = folder_id
    return folder_id


def enviar_pdf(service, nome_arquivo, pdf_bytes, parent_id):
    """Envia um PDF para o Drive e retorna seu ID."""
    validar_drive_id(parent_id)
    metadata = {'name': nome_arquivo, 'parents': [parent_id]}
    media = MediaIoBaseUpload(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        chunksize=2 * 1024 * 1024,
        resumable=True
    )
    arquivo = service.files().create(
        body=metadata,
        media_body=media,
        fields='id'
    ).execute()
    return arquivo['id']


def nome_seguro(s, max_len=50):
    """Converte string para nome de arquivo seguro."""
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'[^a-zA-Z0-9 _-]', '', s)
    s = re.sub(r'\s+', '_', s.strip())
    return s[:max_len]


# ─────────────────────────────────────────────────────────────────────────────
# PROGRESSO
# ─────────────────────────────────────────────────────────────────────────────

def carregar_progresso():
    """
    [FIX-BAIXO] Carrega e valida o arquivo de progresso.
    Verifica o schema esperado antes de usar os dados.
    """
    if not os.path.exists(PROGRESS_FILE):
        return {'concluidos': [], 'falhas': []}

    try:
        with open(PROGRESS_FILE, encoding='utf-8') as f:
            dados = json.load(f)

        # Validação de schema
        if not isinstance(dados, dict):
            raise ValueError("progresso.json deve ser um objeto JSON.")
        if not isinstance(dados.get('concluidos'), list):
            raise ValueError("Campo 'concluidos' deve ser uma lista.")
        if not isinstance(dados.get('falhas'), list):
            raise ValueError("Campo 'falhas' deve ser uma lista.")

        # Valida que os IDs têm formato correto
        for fid in dados['concluidos'] + dados['falhas']:
            if not isinstance(fid, str) or not _DRIVE_ID_RE.match(fid):
                raise ValueError(f"ID inválido no progresso: {fid!r}")

        return dados

    except (json.JSONDecodeError, ValueError) as e:
        logging.warning(f"progresso.json corrompido ou inválido ({e}). Reiniciando progresso.")
        return {'concluidos': [], 'falhas': []}


def salvar_progresso(progresso):
    """Salva o progresso e restringe permissões do arquivo."""
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progresso, f, ensure_ascii=False, indent=2)
    _set_arquivo_privado(PROGRESS_FILE)


# ─────────────────────────────────────────────────────────────────────────────
# PROCESSAMENTO DE UM CLIENTE
# ─────────────────────────────────────────────────────────────────────────────

def processar_cliente(service, pasta_cliente, new_clientes_id):
    """Processa um cliente: baixa PDFs, classifica e envia para nova estrutura."""
    nome     = pasta_cliente['name'].strip()
    pasta_id = pasta_cliente['id']

    # [FIX-MÉDIO] Sanitiza o nome antes de logar (previne Log Injection)
    nome_log = sanitizar_log(nome)
    logging.info(f"  Cliente: {nome_log}")

    # Valida o ID da pasta do cliente
    try:
        validar_drive_id(pasta_id)
    except ValueError as e:
        logging.error(f"  ID de pasta inválido para cliente {nome_log}: {e}")
        return False

    pdfs      = listar_pdfs(service, pasta_id)
    subpastas = listar_subpastas(service, pasta_id)
    for sp in subpastas:
        try:
            validar_drive_id(sp['id'])
            pdfs.extend(listar_pdfs(service, sp['id']))
        except ValueError:
            continue  # Ignora subpastas com IDs suspeitos

    if not pdfs:
        logging.warning(f"  Nenhum PDF encontrado para {nome_log} — pulando")
        return True

    logging.info(f"  {len(pdfs)} PDF(s) encontrado(s)")

    nome_novo     = nome.upper().strip()
    pasta_nova_id = obter_ou_criar_pasta(service, nome_novo, new_clientes_id)

    ids_subpastas = {}
    for sf in SUBFOLDERS:
        ids_subpastas[sf] = obter_ou_criar_pasta(service, sf, pasta_nova_id)

    sucesso_total = True
    for pdf_info in pdfs:
        nome_pdf_log = sanitizar_log(pdf_info.get('name', 'sem_nome'))
        try:
            logging.info(f"  Baixando: {nome_pdf_log}")

            # [FIX-MÉDIO] Passa o tamanho para verificação antes do download
            tamanho = pdf_info.get('size')
            pdf_bytes = baixar_arquivo(service, pdf_info['id'], tamanho_bytes=tamanho)

            categorias = separar_pdf_por_categoria(pdf_bytes, nome_arquivo=nome_pdf_log)

            for cat, cat_bytes in categorias.items():
                pasta_destino = CAT_TO_FOLDER.get(cat, '01_DOCUMENTOS')
                destino_id    = ids_subpastas[pasta_destino]
                nome_arquivo  = f"{nome_seguro(nome)}_{nome_seguro(cat)}.pdf"
                logging.info(f"  → {pasta_destino}/{nome_arquivo}")
                enviar_pdf(service, nome_arquivo, cat_bytes, destino_id)

        except ValueError as e:
            # [FIX-BAIXO] Erros de validação logados de forma controlada
            logging.warning(f"  Ignorado {nome_pdf_log}: {e}")
        except Exception as e:
            # [FIX-BAIXO] Não expõe detalhes internos do sistema no log
            logging.error(f"  Erro ao processar {nome_pdf_log}: {type(e).__name__}")
            logging.debug(f"  Detalhe: {e}")  # Detalhes só em modo DEBUG
            sucesso_total = False

    return sucesso_total


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ]
    )
    # Restringe permissão do log também (contém nomes de clientes)
    _set_arquivo_privado(LOG_FILE) if os.path.exists(LOG_FILE) else None

    print("=" * 60)
    print("  Organizador de Drive — Sara Rocha Advocacia")
    print("=" * 60)

    # Valida os IDs de configuração na inicialização
    try:
        validar_drive_id(OLD_CLIENTES_ID)
        validar_drive_id(NEW_CLIENTES_ID)
    except ValueError as e:
        print(f"\nERRO de configuração: {e}")
        print("Verifique o arquivo config.py ou as variáveis de ambiente.")
        sys.exit(1)

    logging.info("Autenticando com Google Drive...")
    service = autenticar()
    logging.info("Autenticado com sucesso")

    progresso = carregar_progresso()
    ja_feitos = set(progresso['concluidos'])

    logging.info("Listando clientes...")
    todos_clientes = listar_subpastas(service, OLD_CLIENTES_ID)
    total          = len(todos_clientes)
    logging.info(f"Total de clientes: {total}")

    pendentes = [c for c in todos_clientes if c['id'] not in ja_feitos]
    logging.info(f"Já processados: {len(ja_feitos)} | Pendentes: {len(pendentes)}\n")

    if not pendentes:
        logging.info("Todos os clientes já foram processados!")
        return

    for i, cliente in enumerate(pendentes, 1):
        logging.info(f"\n[{i}/{len(pendentes)}] {'-' * 40}")
        try:
            ok = processar_cliente(service, cliente, NEW_CLIENTES_ID)
            if ok:
                progresso['concluidos'].append(cliente['id'])
                if cliente['id'] in progresso['falhas']:
                    progresso['falhas'].remove(cliente['id'])
                logging.info(f"  Concluído: {sanitizar_log(cliente['name'])}")
            else:
                if cliente['id'] not in progresso['falhas']:
                    progresso['falhas'].append(cliente['id'])
                logging.warning(f"  Concluído com erros: {sanitizar_log(cliente['name'])}")

        except Exception as e:
            logging.error(f"  Erro crítico: {type(e).__name__}")
            logging.debug(f"  Detalhe: {e}")
            if cliente['id'] not in progresso['falhas']:
                progresso['falhas'].append(cliente['id'])

        salvar_progresso(progresso)
        time.sleep(0.3)

    logging.info(f"\n{'=' * 60}")
    logging.info(f"  CONCLUÍDO!")
    logging.info(f"  Processados com sucesso: {len(progresso['concluidos'])}/{total}")
    logging.info(f"  Com falhas: {len(progresso['falhas'])}")
    logging.info(f"{'=' * 60}")


if __name__ == '__main__':
    main()
