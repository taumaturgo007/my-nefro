from click import prompt
import google.generativeai as genai
import os
import threading
import time
import requests
import pytesseract
import re
import sqlite3
from datetime import datetime
from PyPDF2 import PdfReader
from flask import Flask, render_template, request, redirect, url_for
from dotenv import load_dotenv

# Carrega variáveis do .env
load_dotenv()

# Acesse as variáveis
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('models/gemini-flash-latest')
SECRET_KEY = os.getenv('SECRET_KEY')

pytesseract.pytesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Configuração do aplicativo Flask
app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Criação do banco de dados
DATABASE = 'exames.db'

# -------------------------------
# Funções Auxiliares
# -------------------------------

def init_db():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        # Tabelas existentes
        cursor.execute('''CREATE TABLE IF NOT EXISTS exames (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            nome TEXT,
                            valor TEXT,
                            data_coleta TEXT,
                            UNIQUE(nome, data_coleta)
                          )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS analises_ia (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            conteudo TEXT,
                            data_geracao DATETIME DEFAULT CURRENT_TIMESTAMP
                          )''')
        
        # --- NOVA TABELA DE MEDICAÇÕES ---
        cursor.execute('''CREATE TABLE IF NOT EXISTS medicacoes (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            nome TEXT,
                            detalhes TEXT
                          )''')
        # --- NOVA TABELA: PERFIL (O erro acontece porque essa tabela ou dados faltam) ---
        cursor.execute('''CREATE TABLE IF NOT EXISTS perfil (
                            id INTEGER PRIMARY KEY CHECK (id = 1), 
                            nome TEXT,
                            diagnostico TEXT,
                            peso TEXT,
                            altura TEXT,
                            pressao TEXT
                          )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS eventos (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            data_evento TEXT,
                            descricao TEXT
                          )''')
        
        # Cria um perfil padrão automaticamente se estiver vazio
        cursor.execute("INSERT OR IGNORE INTO perfil (id, nome, diagnostico, peso, altura, pressao) VALUES (1, 'Seu Nome', 'Diagnóstico aqui', '00', '0.00', '120/80')")
        conn.commit()

# 2. NOVA FUNÇÃO AUXILIAR (Para a IA ler o diário)
def get_eventos_texto():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        # Pega os últimos 10 eventos ordenados por data
        cursor.execute("SELECT data_evento, descricao FROM eventos ORDER BY data_evento DESC LIMIT 30")
        eventos = cursor.fetchall()
        
    if not eventos:
        return "Nenhum evento adverso ou observação registrada no período."
    
    # Formata: "2025-10-31: Tive febre alta"
    lista_texto = [f"- {evt[0]}: {evt[1]}" for evt in eventos]
    return "\n".join(lista_texto)

# Função auxiliar para formatar HTML simples

def markdown_to_html(texto):
    if not texto:
        return ""
    
    import re
    
    # 1. TRATAR TABELAS (NOVO)
    # Procura blocos que começam com | e têm pelo menos duas linhas
    linhas = texto.split('\n')
    novo_texto = []
    em_tabela = False
    
    for linha in linhas:
        if '|' in linha:
            if not em_tabela:
                novo_texto.append('<div class="table-responsive"><table class="table table-sm table-bordered table-striped mt-2" style="font-size: 12px; background: white;">')
                em_tabela = True
            
            # Limpa as barras e transforma em colunas de tabela
            # Ignora linhas de separação como |:---|
            if '---' in linha:
                continue
                
            colunas = [c.strip() for c in linha.split('|') if c.strip()]
            tag = 'th' if 'Medicação' in linha or 'Exame' in linha else 'td'
            html_linha = '<tr>' + ''.join([f'<{tag}>{c}</{tag}>' for c in colunas]) + '</tr>'
            novo_texto.append(html_linha)
        else:
            if em_tabela:
                novo_texto.append('</table></div>')
                em_tabela = False
            novo_texto.append(linha)
    
    if em_tabela: novo_texto.append('</table></div>')
    texto = '\n'.join(novo_texto)

    # 2. TRATAR TÍTULOS (####)
    texto = re.sub(r'#+\s*(.*?)\s*#*', r'<br><b style="color: #198754; font-size: 16px; display: block; margin-top: 10px;">\1</b>', texto)
    
    # 3. TRATAR NEGRITO (**)
    texto = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', texto)
    
    # 4. TRATAR TÓPICOS (-)
    texto = re.sub(r'^- (.*)', r'• \1', texto, flags=re.MULTILINE)
    
    return texto


# Função auxiliar para formatar medicações para a IA
def get_medicacoes_texto():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT nome, detalhes FROM medicacoes")
        meds = cursor.fetchall()
        
    if not meds:
        return "Nenhuma medicação informada."
    
    # Transforma em texto: "Remédio X (10mg), Remédio Y (2x ao dia)"
    lista_texto = [f"{m[0]} ({m[1]})" for m in meds]
    return ", ".join(lista_texto)

# Função auxiliar para formatar o perfil
def get_perfil_texto():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT nome, diagnostico, peso, altura, pressao FROM perfil WHERE id=1")
            p = cursor.fetchone()
            if p:
                return f"Nome: {p[0]}, Diagnóstico: {p[1]}, Peso: {p[2]}kg, Altura: {p[3]}m, Pressão Arterial: {p[4]}"
        except:
            pass
    return "Dados do perfil não informados."

# Função para converter a data de AAAA-MM-DD para DD/MM/AAAA
def format_date_for_db(date_str):
    try:
        # Converte a data do formato AAAA-MM-DD para DD/MM/AAAA
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj.strftime("%d/%m/%Y")
    except ValueError:
        return date_str  # Retorna a data original se não for possível converter


def extract_text_from_pdf(pdf_path):
    text = ""
    with open(pdf_path, 'rb') as file:
        reader = PdfReader(file)
        for page in reader.pages:
            if page and page.extract_text():
                text += page.extract_text()
    return text



# Função para limpar e normalizar texto
def clean_extracted_text(text):
    text = text.replace('\n', ' ')  # Remove quebras de linha
    text = re.sub(r'\s+', ' ', text)  # Substitui múltiplos espaços por um único
    text = text.lower()  # Normaliza para minúsculas
    return text


# Padrão para extrair a data de atendimento
data_atendimento_pattern = r'atendimento[:]?\s*(\d{2}/\d{2}/\d{4})'

# Definição de patterns no escopo global
patterns = {
    'Creatinina': r'creatinina[:\s]*([\d,.]+)\s*mg/dl',
    'Ureia': r'ur[éeÉÉ]ia[:\s-]*([\d,.]+)\s*mg/dl',
    'eRFG': r'erfg:\s*>?\s*([\d,.]+)\s*ml/min/1,73\s*m²',
    'Colesterol LDL': r'ldl[-\s]*colesterol[:\s-]*([\d,.]+)\s*mg/dl',
    'Triglicerídeos': r'triglic[ée]r[ií]des[:\s-]*([\d,.]+)\s*mg/dl',  # Ajustado
    'Sódio': r's[oó]dio[:\s]*([\d,.]+)\s*meq/l',
    'Potássio': r'pot[áa]ssio[:\s]*([\d,.]+)\s*meq/l',
    'Cálcio': r'c[aá]lcio[:\s]*([\d,.]+)\s*mg/dl',
    'Ácido Úrico': r'[áa]cido [úu]rico[:\s]*([\d,.]+)\s*mg/dl',
    'Microalbuminúria': r'microalbumin[úu]ria(?:(?!(?:refer|inferior|valor|<))[\s\S])*?([\d,.]+)\s*mg/g',
    
}


# Função para extrair dados específicos dos exames
def extract_exam_data(text):
    #print(f"Texto limpo do PDF: {text}")
    data = {}
    
    # Extrai a data de atendimento
    data_atendimento_match = re.search(data_atendimento_pattern, text, re.IGNORECASE)
    
    if data_atendimento_match:
        data['data_coleta'] = data_atendimento_match.group(1)  # Extrai a data de atendimento
    else:
        data['data_coleta'] = None  # Caso a data não seja encontrada

    # Extrai os valores dos exames
    for key, pattern in patterns.items():
        matches = re.findall(pattern, text)
        if matches:
            valor = matches[-1]  # Pega o último valor encontrado

            # Remove o símbolo ">" do valor da eRFG
            if key == 'eRFG' and '>' in valor:
                valor = valor.replace('>', '').strip()  # Remove ">" e espaços em branco

            data[key] = valor

    # Código de depuração: Verifica se o eRFG foi capturado corretamente
    #print(f"Valores extraídos: {data}")
    if 'eRFG' in data:
        print(f"Valor do eRFG extraído: {data['eRFG']}")
    else:
        print("eRFG não foi capturado.")

    return data

# Função para salvar dados no banco de dados evitando duplicações
def save_to_db(data):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        data_coleta = data.pop('data_coleta', None)  # Remove 'data_coleta' do dicionário e obtém o valor

        if not data_coleta:
            print("Data de coleta não encontrada no PDF.")
            return

        for nome, valor in data.items():
            try:
                # Verifica se já existe um registro com o mesmo nome e data
                cursor.execute('SELECT id FROM exames WHERE nome = ? AND data_coleta = ?', (nome, data_coleta))
                if cursor.fetchone() is None:
                    # Se não existir, insere o novo registro
                    cursor.execute('INSERT INTO exames (nome, valor, data_coleta) VALUES (?, ?, ?)',
                                   (nome, valor, data_coleta))
                    conn.commit()
                else:
                    print(f"Registro duplicado ignorado: {nome} - {data_coleta}")
            except sqlite3.IntegrityError as e:
                print(f"Erro ao inserir no banco de dados: {e}")

def salvar_analise_ia_db(texto_analise):
    """Salva a resposta da IA no banco de dados"""
    if not texto_analise: return
    
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO analises_ia (conteudo) VALUES (?)', (texto_analise,))
        conn.commit()

def recuperar_ultima_analise_ia():
    """Busca a última análise salva para exibir na Home"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        # Pega a última análise gerada (ordenada por ID decrescente)
        cursor.execute('SELECT conteudo FROM analises_ia ORDER BY id DESC LIMIT 1')
        resultado = cursor.fetchone()
        
        if resultado:
            return resultado[0]
        else:
            return None # Retorna None se nunca foi gerada nenhuma análise

# Função para gerar uma análise dos exames
def gerar_analise():
    analise = {}
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT nome, valor, data_coleta 
            FROM exames 
            ORDER BY substr(data_coleta, 7, 4) DESC, 
                     substr(data_coleta, 4, 2) DESC, 
                     substr(data_coleta, 1, 2) DESC
        ''')
        exames = cursor.fetchall()
        
        dados_por_exame = {}
        for nome, valor, data in exames:
            if nome not in dados_por_exame:
                dados_por_exame[nome] = []
            try:
                # Armazena valor convertido e data
                dados_por_exame[nome].append({
                    'valor': float(valor.replace(",", ".")),
                    'data': data
                })
            except ValueError:
                continue 
        
        for nome, valores in dados_por_exame.items():
            #nome_exibicao = 'eRFG' if nome == 'TFG' else nome  # Mapeia TFG para eRFG
            
            if len(valores) >= 2:
                # Compara os valores numéricos (não os dicionários inteiros)
                valor_atual = valores[0]['valor']
                valor_anterior = valores[1]['valor']
                
                tendencia = "estável"
                if valor_atual > valor_anterior:
                    tendencia = "aumentando"
                elif valor_atual < valor_anterior:
                    tendencia = "diminuindo"
                    
                analise[nome] = f"O último exame de {nome} está {tendencia}. Último valor: {valor_atual} em {valores[0]['data']}"
            else:
                analise[nome] = f"Único valor disponível para {nome}: {valores[0]['valor']} em {valores[0]['data']}"
    
    return analise

# Gera uma análise geral consolidada usando os dados extraídos

def gerar_analise_geral(analise):
    """
    Gera análise geral com tratamento especial para eRFG
    """
    if not analise:
        return {"titulo": "Análise Completa (Data desconhecida)", "itens": ["Nenhum dado disponível para análise"]}

    # Dicionário de referências atualizado
    referencia = {
        'Creatinina': {'normal': (0.7, 1.3), 'unidade': 'mg/dL', 'acima_texto': 'elevada', 'abaixo_texto': 'baixa'},
        'Ureia': {'normal': (10, 50), 'unidade': 'mg/dL', 'acima_texto': 'elevada', 'abaixo_texto': 'baixa'},
        'eRFG': {'limite': 60, 'unidade': 'mL/min/1,73 m²', 'acima_texto': 'normal', 'abaixo_texto': 'reduzida'},
        'Colesterol LDL': {'normal': (0, 100), 'unidade': 'mg/dL', 'acima_texto': 'elevado', 'abaixo_texto': 'desejável'},
        'Triglicerídeos': {'normal': (0, 150), 'unidade': 'mg/dL', 'acima_texto': 'elevados', 'abaixo_texto': 'normal'},
        'Sódio': {'normal': (135, 145), 'unidade': 'meq/L', 'acima_texto': 'elevado', 'abaixo_texto': 'baixo'},
        'Potássio': {'normal': (3.5, 5.0), 'unidade': 'meq/L', 'acima_texto': 'elevado', 'abaixo_texto': 'baixo'},
        'Ácido Úrico': {'normal': (3.4, 7.0), 'unidade': 'mg/dL', 'acima_texto': 'elevado', 'abaixo_texto': 'normal'},
        'Microalbuminúria': {'normal': (0, 30), 'unidade': 'mcg/mg', 'acima_texto': 'elevada', 'abaixo_texto': 'normal'}
    }

    # Extrai todas as datas disponíveis
    datas = []
    for descricao in analise.values():
        if ' em ' in descricao:
            try:
                data_str = descricao.split(' em ')[-1].strip()
                # Converte a string de data para objeto datetime
                dia, mes, ano = map(int, data_str.split('/'))
                data_obj = datetime(ano, mes, dia)
                datas.append((data_obj, data_str))
            except Exception as e:
                print(f"Erro ao processar data: {e}")
                continue

    # Pega a data mais recente
    data_recente = "Data desconhecida"
    if datas:
        try:
            data_recente = max(datas, key=lambda x: x[0])[1]
        except:
            data_recente = datas[0][1] if datas else "Data desconhecida"

    # Processa cada exame
    analise_pontos = []
    
    for exame in referencia.keys():
        if exame in analise:
            try:
                # Extrai o valor numérico
                valor_str = analise[exame].split("Último valor: ")[1].split()[0]
                valor_str = valor_str.replace(',', '.').rstrip('.')
                valor = float(valor_str)
                
                # Tratamento especial para eRFG 
                if exame == 'eRFG':
                    limite = referencia[exame]['limite']
                    unidade = referencia[exame]['unidade']
                    
                    if valor >= limite:
                        status = f"eRFG > {limite} {unidade} ({referencia[exame]['acima_texto']})"
                    else:
                        status = f"eRFG {valor:.2f} {unidade} ({referencia[exame]['abaixo_texto']})"
                else:
                    # Análise padrão para outros exames
                    min_ref, max_ref = referencia[exame]['normal']
                    unidade = referencia[exame]['unidade']
                    
                    if valor < min_ref:
                        status = f"{exame} {valor:.2f} {unidade} ({referencia[exame]['abaixo_texto']})"
                    elif valor > max_ref:
                        status = f"{exame} {valor:.2f} {unidade} ({referencia[exame]['acima_texto']})"
                    else:
                        status = f"{exame} {valor:.2f} {unidade} (normal)"
                
                analise_pontos.append(status)
                
            except Exception as e:
                print(f"Erro ao processar {exame}: {str(e)}")
                continue

    # Ordena os resultados alfabeticamente
    analise_pontos.sort()
    
    return {
        "titulo": f"Análise Completa ({data_recente})",
        "itens": analise_pontos
    }

# Função para gerar uma análise médica usando a API do DeepSeek
def gerar_analise_medica(exames_dict, medicacoes_texto, perfil_texto, eventos_texto): # <--- Adicionei perfil_texto aqui
    
    # Monta o texto dos exames
    exames_texto = ""
    for exame, dados in exames_dict.items():
        valores = dados['valores']
        labels = dados['labels']
        if valores:
            ultimo_valor = valores[-1]
            data_ultimo = labels[-1]
            historico = ", ".join([f"{v} ({d})" for v, d in zip(valores[-5:], labels[-5:])])
            exames_texto += f"- {exame}: Último {ultimo_valor} em {data_ultimo}. Histórico recente: [{historico}]\n"

    # O Prompt agora inclui o PERFIL
    prompt = f"""
    Como um nefrologista especialista. Analise o caso deste paciente para fornecer uma avaliação médica:

    === DADOS DO PACIENTE ===
    {perfil_texto}

    === MEDICAÇÕES EM USO ===
    {medicacoes_texto}

    === DIÁRIO CLÍNICO (Sintomas e Ocorrências) ===
    {eventos_texto}

    === RESULTADOS DE EXAMES ===
    {exames_texto}

    POR FAVOR, FORNEÇA:
    1. Uma análise concisa do estado atual baseada nos últimos exames e no diagnóstico do paciente.
    2. Comente se a pressão arterial e o peso estão adequados para o quadro renal.
    3. Verifique se as medicações atuais fazem sentido para os resultados apresentados.
    4. Cruse os dados do diário clínico com os exames e medicações.
    5. Dê 3 recomendações práticas de curto prazo.
    
    Use tom profissional mas acolhedor. Responda em Português do Brasil.
    """
    print(f"--- PROMPT ENVIADO ---\n{prompt}\n----------------------")
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Erro ao gerar análise: {e}"

#Função para manter o app ativo no Render
def keep_alive():
    while True:
        try:
            url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/keepalive"
            requests.get(url, timeout=5)
        except:
            pass
        time.sleep(300)  # Ping a cada 5 minutos

@app.route('/keepalive')
def alive():
    return "App ativo!"

# Inicia quando o app rodar no Render
if os.environ.get('RENDER'):
    threading.Thread(target=keep_alive).start()


# -------------------------------
# Rotas da Aplicação
# -------------------------------
@app.route('/')
def index():
    return redirect(url_for('home'))

from datetime import datetime

@app.route('/home')
def home():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        
        # --- 1. Busca os dados dos EXAMES (Mantido igual) ---
        exames = {}
        for exame in patterns.keys():
            cursor.execute('SELECT data_coleta, valor FROM exames WHERE nome = ?', (exame,))
            dados = cursor.fetchall()
            
            labels = []
            valores = []
            for row in dados:
                data_coleta = str(row[0]) if row[0] else None
                valor = row[1]
                
                if valor and valor != '-':
                    try:
                        valor_float = float(valor.replace(',', '.'))
                        valores.append(valor_float)
                        labels.append(data_coleta)
                    except ValueError:
                        pass
            
            if labels and valores:
                combined = list(zip(labels, valores))
                combined.sort(key=lambda x: datetime.strptime(x[0], '%d/%m/%Y'))
                labels, valores = zip(*combined)
            
            exames[exame] = {
                'labels': labels,
                'valores': valores
            }

        # --- 4. Ordenar Exames e Calcular Média Móvel (Moving Average) ---
        priority = ['Creatinina', 'Microalbuminúria']
        ordered_exames = {}
        
        # Cálculo da Média Móvel para Creatinina (Janela de 3)
        if 'Creatinina' in exames and len(exames['Creatinina']['valores']) >= 2:
            vals = exames['Creatinina']['valores']
            window = 3
            # Calcula a média dos últimos 3 pontos para cada ponto do gráfico
            ma = [round(sum(vals[max(0, i-window+1):i+1]) / len(vals[max(0, i-window+1):i+1]), 2) for i in range(len(vals))]
            exames['Creatinina']['valores_ma'] = ma

        # Prioriza Creatinina e Microalbuminúria no topo para ficarem lado a lado
        for p in priority:
            if p in exames:
                ordered_exames[p] = exames[p]
        
        # Adiciona os outros exames logo abaixo
        for k, v in exames.items():
            if k not in ordered_exames:
                ordered_exames[k] = v
        
        exames = ordered_exames

        # --- 2. Busca as MEDICAÇÕES no Banco (<<< MUDANÇA AQUI) ---
        # Antes estava fixo em texto, agora vem da tabela nova
        cursor.execute("SELECT id, nome, detalhes FROM medicacoes")
        lista_medicacoes = cursor.fetchall()

       # 3. Busca PERFIL (AQUI ESTAVA FALTANDO!)
        try:
            cursor.execute("SELECT nome, diagnostico, peso, altura, pressao FROM perfil WHERE id=1")
            p = cursor.fetchone()
            if p:
                perfil_data = {
                    'nome': p[0], 'diagnostico': p[1], 
                    'peso': p[2], 'altura': p[3], 'pressao': p[4]
                }
            else:
                # Fallback caso a tabela exista mas esteja vazia
                perfil_data = {'nome': 'Usuário', 'diagnostico': '-', 'peso': '-', 'altura': '-', 'pressao': '-'}
        except:
            # Fallback caso a tabela ainda não exista (previne crash)
            perfil_data = {'nome': 'Atualize a Página', 'diagnostico': '', 'peso': '', 'altura': '', 'pressao': ''}

        # --- 3. Gera as ANÁLISES (Mantido igual) ---
        analise = gerar_analise()
        analise_geral = gerar_analise_geral(analise)
        
        # Busca a análise da IA salva no banco
        
        analise_bruta = recuperar_ultima_analise_ia()
        # Se existir análise, formatamos. Se não, enviamos string vazia ou aviso.
        analise_medica = markdown_to_html(analise_bruta) if analise_bruta else "Nenhuma análise gerada."


        if not analise_medica:
            analise_medica = "Nenhuma análise gerada por IA ainda. Clique no botão de atualizar análise."


    # --- NOVO: BUSCA OS EVENTOS PARA EXIBIR NA TELA ---
        cursor.execute("SELECT id, data_evento, descricao FROM eventos ORDER BY data_evento DESC")
        lista_eventos = cursor.fetchall()

# --- BLOCO NOVO: Cria o gráfico "Creatinina Tempo Real" ---
    if 'Creatinina' in exames:
        # 1. Cria uma cópia dos dados da Creatinina
        dados_tempo_real = exames['Creatinina'].copy()
        
        # 2. Reorganiza o dicionário para inserir LOGO DEPOIS de Microalbuminúria
        nova_ordem = {}
        chaves = list(exames.keys())
        
        # Adiciona todos os exames na ordem original
        for k in chaves:
            nova_ordem[k] = exames[k]
            # Se acabou de adicionar Microalbuminúria, insere o Tempo Real
            if k == 'Microalbuminúria':
                nova_ordem['Creatinina Tempo Real'] = dados_tempo_real
        
        # Se Microalbuminúria não existia, adiciona no final por segurança
        if 'Creatinina Tempo Real' not in nova_ordem:
            nova_ordem['Creatinina Tempo Real'] = dados_tempo_real
            
        exames = nova_ordem


    return render_template('home_plotly.html', 
                           exames=exames, # (supondo que a variável exames existe do código anterior)
                           analise=analise, 
                           analise_geral=analise_geral,
                           analise_medica=analise_medica,
                           medicacoes=lista_medicacoes,
                           perfil=perfil_data, # (supondo que perfil_data existe)
                           eventos=lista_eventos) # <--- ADICIONE ISSO

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return redirect(request.url)

    file = request.files['file']
    if file.filename == '':
        return redirect(request.url)

    if file:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filepath)
        extracted_text = extract_text_from_pdf(filepath)
        clean_text = clean_extracted_text(extracted_text)
        exam_data = extract_exam_data(clean_text)
        save_to_db(exam_data)
        return redirect(url_for('exams_summary'))


@app.route('/exams')
def view_exams():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, nome, valor, data_coleta FROM exames')
        exams = cursor.fetchall()
        exam_options = list(patterns.keys())  # Lista de exames para a combo

    return render_template('exams_editable.html', exams=exams, exam_options=exam_options)

@app.route('/add_exam', methods=['POST'])
def add_exam():
    # Obtém os dados do formulário
    nome = request.form.get('nome')
    valor = request.form.get('valor')
    data_coleta = request.form.get('data')

    # Converte a data de AAAA-MM-DD para DD/MM/AAAA (se necessário)
    data_coleta = format_date_for_db(data_coleta)

    # Insere os dados no banco de dados
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO exames (nome, valor, data_coleta)
                          VALUES (?, ?, ?)''', (nome, valor, data_coleta))
        conn.commit()

    # Redireciona para a página de visualização de exames
    return redirect(url_for('view_exams'))

@app.route('/delete_exam/<int:exam_id>', methods=['GET'])
def delete_exam(exam_id):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM exames WHERE id = ?', (exam_id,))
        conn.commit()
    
    return redirect(url_for('view_exams'))

@app.route('/update_exams', methods=['POST'])
def update_exams():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        for exam_id in request.form.getlist('update'):
            nome = request.form.get(f'nome_{exam_id}')
            valor = request.form.get(f'valor_{exam_id}')
            data_coleta = request.form.get(f'data_{exam_id}')
            cursor.execute('''UPDATE exames SET nome = ?, valor = ?, data_coleta = ? WHERE id = ?''',
                           (nome, valor, data_coleta, exam_id))
        conn.commit()
    
    return redirect(url_for('view_exams'))

@app.route('/exams_summary')
def exams_summary():
    # Inicializa variáveis padrão para evitar erros se o banco estiver vazio
    summary_list = []
    perfil_data = {'nome': 'Usuário', 'diagnostico': '', 'peso': '-', 'altura': '-', 'pressao': '-'}

    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        
        # --- 1. BUSCAR E PROCESSAR EXAMES ---
        cursor.execute("SELECT * FROM exames")
        data = cursor.fetchall()
        
        # Dicionário para agrupar exames pela data
        # Estrutura: { '10/10/2023': { 'Creatinina': '1.2', 'Ureia': '40' }, ... }
        exams_by_date = {}
        
        for row in data:
            # row[1]=nome, row[2]=valor, row[3]=data_coleta
            # Ajuste os índices conforme sua tabela (assumindo id, nome, valor, data)
            nome_exame = row[1]
            valor_exame = row[2]
            data_coleta = row[3]
            
            if data_coleta: # Só processa se tiver data
                if data_coleta not in exams_by_date:
                    exams_by_date[data_coleta] = {}
                exams_by_date[data_coleta][nome_exame] = valor_exame
            
        # Transforma o dicionário em uma lista para o HTML
        for date, exams in exams_by_date.items():
            entry = {'Data': date}
            entry.update(exams) # Adiciona todos os exames dessa data no dicionário
            summary_list.append(entry)
            
        # Ordena da data mais recente para a mais antiga
        try:
            summary_list.sort(key=lambda x: datetime.strptime(x['Data'], '%d/%m/%Y'), reverse=True)
        except Exception as e:
            print(f"Erro ao ordenar datas: {e}")
            # Se der erro de data, mantém a ordem original sem quebrar o site

        # --- 2. BUSCAR DADOS DO PERFIL ---
        try:
            cursor.execute("SELECT nome, diagnostico, peso, altura, pressao FROM perfil WHERE id=1")
            p = cursor.fetchone()
            if p:
                perfil_data = {
                    'nome': p[0], 
                    'diagnostico': p[1], 
                    'peso': p[2], 
                    'altura': p[3], 
                    'pressao': p[4]
                }
        except Exception as e:
            print(f"Erro ao buscar perfil: {e}")

    # Retorna o template com TODAS as variáveis definidas
    return render_template('exams_summary.html', 
                           exams_summary=summary_list, 
                           perfil=perfil_data)
@app.route('/update_summary', methods=['POST'])
def update_summary():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        
        # --- 1. ATUALIZAÇÃO DAS LINHAS EXISTENTES ---
        # Identifica quais linhas antigas foram editadas
        indices = set()
        for key in request.form:
            if key.startswith('data_original_'):
                indices.add(key.split('_')[-1])
        
        for idx in indices:
            original_date = request.form.get(f'data_original_{idx}')
            new_date = request.form.get(f'data_{idx}')
            target_date = new_date if new_date else original_date
            
            # Mapeamento dos campos de edição existentes
            campos = {
                'Creatinina': f'creatinina_{idx}',
                'eRFG': f'erfg_{idx}',
                'Ureia': f'ureia_{idx}',
                'Relação Proteína/Creatinina': f'relacao_{idx}',
                'Triglicerídeos': f'triglicerideos_{idx}',
                'Colesterol LDL': f'ldl_{idx}',
                'Ácido Úrico': f'acido_urico_{idx}',
                'Sódio': f'sodio_{idx}',
                'Potássio': f'potassio_{idx}',
                'Cálcio': f'calcio_{idx}',
                'Microalbuminúria': f'microalbuminuria_{idx}'
            }

            # Se a data mudou, remove os registros da data antiga para evitar duplicata
            if original_date and original_date != target_date:
                 cursor.execute("DELETE FROM exames WHERE data_coleta = ?", (original_date,))

            # Atualiza ou Insere os valores editados
            for nome_db, input_html in campos.items():
                valor = request.form.get(input_html)
                if valor and valor.strip() and valor.strip() != '-':
                    cursor.execute('UPDATE exames SET valor = ? WHERE nome = ? AND data_coleta = ?', (valor, nome_db, target_date))
                    if cursor.rowcount == 0:
                        cursor.execute('INSERT INTO exames (nome, valor, data_coleta) VALUES (?, ?, ?)', (nome_db, valor, target_date))

        # --- 2. INSERÇÃO DE NOVAS LINHAS (MANUAL) ---
        # Pega as listas de todos os novos campos adicionados
        novas_datas = request.form.getlist('nova_data[]')
        
        # Mapeamento: (Nome no Banco) : (Nome do Input "nova_...")
        mapa_novos = {
            'Creatinina': 'nova_creatinina[]',
            'eRFG': 'nova_erfg[]',
            'Ureia': 'nova_ureia[]',
            'Relação Proteína/Creatinina': 'nova_relacao[]',
            'Triglicerídeos': 'nova_triglicerideos[]',
            'Colesterol LDL': 'nova_ldl[]',
            'Ácido Úrico': 'nova_acido_urico[]',
            'Sódio': 'nova_sodio[]',
            'Potássio': 'nova_potassio[]',
            'Cálcio': 'nova_calcio[]',
            'Microalbuminúria': 'nova_microalbuminuria[]'
        }
        
        # Itera sobre cada nova linha adicionada
        for i, data_coleta in enumerate(novas_datas):
            # Se a data estiver vazia, ignoramos essa linha
            if not data_coleta or not data_coleta.strip(): continue
            
            for nome_db, nome_input in mapa_novos.items():
                # Pega a lista de valores daquele exame específico
                lista_valores = request.form.getlist(nome_input)
                
                # Garante que o índice existe e pega o valor
                if i < len(lista_valores):
                    valor = lista_valores[i]
                    # Se tiver valor preenchido, insere no banco
                    if valor and valor.strip() and valor.strip() != '-':
                         cursor.execute('INSERT INTO exames (nome, valor, data_coleta) VALUES (?, ?, ?)', (nome_db, valor, data_coleta))
        
        conn.commit()

    return redirect(url_for('exams_summary'))

@app.route('/gerar-nova-analise')
def trigger_analise_ia():
    # 1. Busca Exames (Lógica existente)
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        exames_dict = {}
        for exame in patterns.keys():
            cursor.execute('SELECT data_coleta, valor FROM exames WHERE nome = ?', (exame,))
            dados = cursor.fetchall()
            labels = []
            valores = []
            for row in dados:
                if row[1] and row[1] != '-':
                    try:
                        valores.append(float(row[1].replace(',', '.')))
                        labels.append(str(row[0]))
                    except: pass
            
            # Ordena
            if labels and valores:
                combined = sorted(zip(labels, valores), key=lambda x: datetime.strptime(x[0], '%d/%m/%Y'))
                labels, valores = zip(*combined)
                
            exames_dict[exame] = {'labels': labels, 'valores': valores}

    # 2. Busca Medicações e Perfil (NOVOS)
    medicacoes_texto = get_medicacoes_texto()
    perfil_texto = get_perfil_texto() # <--- Pega os dados do banco

    
    # ---3. NOVO: PEGA O DIÁRIO ---
    eventos_texto = get_eventos_texto()

    # 4. Passa tudo para a função
    nova_analise = gerar_analise_medica(exames_dict, medicacoes_texto, perfil_texto, eventos_texto)

    salvar_analise_ia_db(nova_analise)

    return redirect(url_for('home'))

@app.route('/adicionar_medicacao', methods=['POST'])
def adicionar_medicacao():
    nome = request.form.get('nome')
    detalhes = request.form.get('detalhes') # Ex: 50mg 2x ao dia
    
    if nome:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO medicacoes (nome, detalhes) VALUES (?, ?)', (nome, detalhes))
            conn.commit()
    return redirect(url_for('home'))

@app.route('/deletar_medicacao/<int:id>')
def deletar_medicacao(id):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM medicacoes WHERE id = ?', (id,))
        conn.commit()
    return redirect(url_for('home'))

# --- NOVA ROTA PARA SALVAR O PERFIL ---
@app.route('/atualizar_perfil', methods=['POST'])
def atualizar_perfil():
    nome = request.form.get('nome')
    diagnostico = request.form.get('diagnostico')
    peso = request.form.get('peso')
    altura = request.form.get('altura')
    pressao = request.form.get('pressao')
    
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('''UPDATE perfil SET 
                          nome=?, diagnostico=?, peso=?, altura=?, pressao=? 
                          WHERE id=1''', 
                       (nome, diagnostico, peso, altura, pressao))
        conn.commit()
    return redirect(url_for('home'))

# --- ROTA PARA EXCLUIR UMA DATA INTEIRA ---
@app.route('/excluir_data_resumo')
def excluir_data_resumo():
    # Pega a data da URL (ex: ?data=31/10/2025)
    data_coleta = request.args.get('data')
    
    if data_coleta:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM exames WHERE data_coleta = ?", (data_coleta,))
            conn.commit()
            
    return redirect(url_for('exams_summary'))

# NOVAS ROTAS (Adicionar/Deletar)
@app.route('/adicionar_evento', methods=['POST'])
def adicionar_evento():
    data_evento = request.form.get('data_evento')
    descricao = request.form.get('descricao')
    
    if data_evento and descricao:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO eventos (data_evento, descricao) VALUES (?, ?)', (data_evento, descricao))
            conn.commit()
    return redirect(url_for('home'))

@app.route('/deletar_evento/<int:id>')
def deletar_evento(id):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM eventos WHERE id = ?', (id,))
        conn.commit()
    return redirect(url_for('home'))

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=8080)
   # app.run(debug=True)

