#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Organizador de Drive - Sara Rocha Advocacia
Migra clientes da pasta antiga para nova estrutura organizada por categorias juridicas.
"""
import os, io, json, time, unicodedata, re, sys, logging

try:
    import fitz
except ImportError:
    print("ERRO: pip install PyMuPDF"); sys.exit(1)

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
except ImportError:
    print("ERRO: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"); sys.exit(1)

SCOPES = ['https://www.googleapis.com/auth/drive']
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'
PROGRESS_FILE = 'progresso.json'
LOG_FILE = 'organizador.log'
OLD_CLIENTES_ID = '1g2JIZPlcHOb1F6HghG_aCbD5ummpeDIW'
NEW_CLIENTES_ID = '1QLsUcJP92N0rBjQU6FLAbfUlxrPeWaEH'
SUBFOLDERS = ['01_DOCUMENTOS', '02_PROCESSO JUDICIAL', '03_ADMINISTRATIVO', '04_CONTRATO']
CAT_TO_FOLDER = {
    'Documentos': '01_DOCUMENTOS',
    'Documentos Judiciais': '02_PROCESSO JUDICIAL',
    'Documentos Administrativos': '03_ADMINISTRATIVO',
    'Contrato': '04_CONTRATO',
}
CAT_RULES = [
    {'cat': 'Contrato', 'keywords': ['contrato de prestacao', 'servicos advocaticios', 'clausula', 'contratante', 'contratada', 'honorarios advocaticios', 'rescisao contratual', 'titulo executivo']},
    {'cat': 'Documentos Judiciais', 'keywords': ['procuracao', 'ad judicia', 'outorgante', 'outorgada', 'declaracao de estado de pobreza', 'justica gratuita', 'hipossuficiencia', 'aviso de pericia', 'pericia foi marcada', 'juizado especial federal', 'tutela antecipada']},
    {'cat': 'Documentos Administrativos', 'keywords': ['perfil profissiografico', 'ppp', 'cnis', 'cadastro nacional de informacoes sociais', 'comunicacao de decisao', 'auxilio doenca', 'extrato previdenciario', 'relacoes previdenciarias', 'secao de dados administrativos', 'salario de contribuicao', 'previdencia social', 'instituto nacional do seguro social', 'numero do beneficio', 'deferimento', 'proevi', 'irecol']},
    {'cat': 'Documentos', 'keywords': ['carteira de identidade', 'registro geral', 'naturalidade', 'cedula de identidade', 'total a pagar', 'vencimento', 'fatura', 'fornecimento', 'energia eletrica', 'kwh', 'relatorio medico', 'diagnostico', 'internacoes', 'cirurgia', 'receita medica', 'cid', 'prontuario', 'medico responsavel', 'hospital', 'clinica', 'reabilitacao', 'carteira de trabalho', 'ctps', 'polegar']},
]

def normalizar(texto):
    if not texto: return ''
    nfkd = unicodedata.normalize('NFD', texto.lower())
    t = ''.join(c for c in nfkd if not unicodedata.combining(c))
    for e, c in [('6','o'),('0','o'),('8','e'),('1','l')]: t = t.replace(e, c)
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z\s]', ' ', t)).strip()

def classificar_pagina(texto):
    t = normalizar(texto)
    for r in CAT_RULES:
        if any(kw in t for kw in r['keywords']): return r['cat']
    return 'Documentos'

def separar_pdf_por_categoria(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    cats = [classificar_pagina(doc[i].get_text().strip()) for i in range(len(doc))]
    resultado = {}
    for cat in set(cats):
        nd = fitz.open()
        for i, c in enumerate(cats):
            if c == cat: nd.insert_pdf(doc, from_page=i, to_page=i)
        if len(nd) > 0:
            buf = io.BytesIO(); nd.save(buf); resultado[cat] = buf.getvalue()
        nd.close()
    doc.close()
    return resultado

def autenticar():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        open(TOKEN_FILE, 'w').write(creds.to_json())
    return build('drive', 'v3', credentials=creds)

def listar(service, parent_id, mime):
    items, token = [], None
    while True:
        r = service.files().list(
            q=f"mimeType='{mime}' and '{parent_id}' in parents and trashed=false",
            fields='nextPageToken, files(id, name)', pageSize=1000, pageToken=token
        ).execute()
        items.extend(r.get('files', [])); token = r.get('nextPageToken')
        if not token: break
    return items

def baixar(service, fid):
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=fid), chunksize=4*1024*1024)
    done = False
    while not done: _, done = dl.next_chunk()
    return buf.getvalue()

def get_or_create(service, nome, parent_id, cache=None):
    if cache and nome in cache: return cache[nome]
    r = service.files().list(
        q=f"mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and name='{nome}' and trashed=false",
        fields='files(id)', pageSize=10
    ).execute()
    fid = r['files'][0]['id'] if r.get('files') else service.files().create(
        body={'name': nome, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent_id]},
        fields='id'
    ).execute()['id']
    if cache is not None: cache[nome] = fid
    return fid

def enviar(service, nome, pdf_bytes, parent_id):
    return service.files().create(
        body={'name': nome, 'parents': [parent_id]},
        media_body=MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype='application/pdf', chunksize=2*1024*1024, resumable=True),
        fields='id'
    ).execute()['id']

def nome_seguro(s, n=50):
    s = ''.join(c for c in unicodedata.normalize('NFD', s) if not unicodedata.combining(c))
    return re.sub(r'\s+', '_', re.sub(r'[^a-zA-Z0-9 _-]', '', s).strip())[:n]

def carregar_progresso():
    return json.load(open(PROGRESS_FILE, encoding='utf-8')) if os.path.exists(PROGRESS_FILE) else {'concluidos': [], 'falhas': []}

def salvar_progresso(p):
    json.dump(p, open(PROGRESS_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

def processar_cliente(service, cliente, new_id):
    nome, pid = cliente['name'].strip(), cliente['id']
    logging.info(f"Cliente: {nome}")
    pdfs = listar(service, pid, 'application/pdf')
    for sp in listar(service, pid, 'application/vnd.google-apps.folder'):
        pdfs.extend(listar(service, sp['id'], 'application/pdf'))
    if not pdfs: return True
    nova_id = get_or_create(service, nome.upper(), new_id)
    subs = {sf: get_or_create(service, sf, nova_id) for sf in SUBFOLDERS}
    ok = True
    for pdf in pdfs:
        try:
            for cat, cb in separar_pdf_por_categoria(baixar(service, pdf['id'])).items():
                enviar(service, f"{nome_seguro(nome)}_{nome_seguro(cat)}.pdf", cb, subs[CAT_TO_FOLDER.get(cat, '01_DOCUMENTOS')])
        except Exception as e:
            logging.error(f"Erro: {e}"); ok = False
    return ok

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S',
        handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler(sys.stdout)])
    print("Organizador de Drive - Sara Rocha Advocacia")
    service = autenticar()
    prog = carregar_progresso()
    feitos = set(prog['concluidos'])
    todos = listar(service, OLD_CLIENTES_ID, 'application/vnd.google-apps.folder')
    pendentes = [c for c in todos if c['id'] not in feitos]
    logging.info(f"Total: {len(todos)} | Feitos: {len(feitos)} | Pendentes: {len(pendentes)}")
    for i, c in enumerate(pendentes, 1):
        logging.info(f"[{i}/{len(pendentes)}] {c['name']}")
        try:
            ok = processar_cliente(service, c, NEW_CLIENTES_ID)
            (prog['concluidos'] if ok else prog['falhas']).append(c['id'])
        except Exception as e:
            logging.error(f"Erro critico: {e}"); prog['falhas'].append(c['id'])
        salvar_progresso(prog); time.sleep(0.3)
    logging.info(f"Concluido! {len(prog['concluidos'])}/{len(todos)}")

if __name__ == '__main__':
    main()
