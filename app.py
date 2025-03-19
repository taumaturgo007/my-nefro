from flask import Flask, render_template, request, redirect, url_for
import os
import pytesseract
from PyPDF2 import PdfReader
import re
import sqlite3
from datetime import datetime

pytesseract.pytesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Criação do banco de dados
DATABASE = 'exames.db'

def init_db():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS exames (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            nome TEXT,
                            valor TEXT,
                            data_coleta TEXT,
                            UNIQUE(nome, data_coleta)
                          )''')
        conn.commit()

# Função para converter a data de AAAA-MM-DD para DD/MM/AAAA
def format_date_for_db(date_str):
    try:
        # Converte a data do formato AAAA-MM-DD para DD/MM/AAAA
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj.strftime("%d/%m/%Y")
    except ValueError:
        return date_str  # Retorna a data original se não for possível converter


@app.route('/')
def index():
    return redirect(url_for('home'))

from datetime import datetime

@app.route('/home')
def home():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        
        # Busca os dados para cada exame
        exames = {}
        for exame in patterns.keys():  # Usa os padrões definidos em patterns
            cursor.execute('SELECT data_coleta, valor FROM exames WHERE nome = ?', (exame,))
            dados = cursor.fetchall()
            
            # Formata os dados para o gráfico
            labels = []
            valores = []
            for row in dados:
                # Garante que a data é uma string
                data_coleta = str(row[0]) if row[0] else None
                # Obtém o valor como string
                valor = row[1]
                
                # Verifica se o valor pode ser convertido para float
                if valor and valor != '-':  # Ignora valores vazios ou '-'
                    try:
                        valor_float = float(valor.replace(',', '.'))  # Converte para float
                        valores.append(valor_float)
                        labels.append(data_coleta)
                    except ValueError:
                        # Se não for possível converter, ignora o valor
                        print(f"Valor não numérico ignorado: {valor}")
                        pass
            
            # Ordena as datas e valores com base nas datas
            if labels and valores:
                # Combina labels e valores em uma lista de tuplas
                combined = list(zip(labels, valores))
                # Ordena as tuplas com base na data (convertida para datetime)
                combined.sort(key=lambda x: datetime.strptime(x[0], '%d/%m/%Y'))
                # Separa as labels e valores ordenados
                labels, valores = zip(*combined)
            
            # Adiciona ao dicionário de exames
            exames[exame] = {
                'labels': labels,
                'valores': valores
            }

            analise = gerar_analise()

    # Depuração: Verifique os dados no terminal
   # print("Exames:", exames)
    print("Análise:", analise)

    return render_template('home_plotly.html', exames=exames, analise=analise)

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
    'TFG': r'erfg:\s*>?\s*([\d,.]+)\s*ml/min/1,73\s*m²',
    'Colesterol LDL': r'ldl[-\s]*colesterol[:\s-]*([\d,.]+)\s*mg/dl',
    'Triglicerídeos': r'triglic[ée]r[ií]des[:\s-]*([\d,.]+)\s*mg/dl',  # Ajustado
    'Sódio': r's[oó]dio[:\s]*([\d,.]+)\s*meq/l',
    'Potássio': r'pot[áa]ssio[:\s]*([\d,.]+)\s*meq/l',
    'Cálcio': r'c[aá]lcio[:\s]*([\d,.]+)\s*mg/dl',
    'Ácido Úrico': r'[áa]cido [úu]rico[:\s]*([\d,.]+)\s*mg/dl',
    'Microalbuminúria': r'microalbumin[úu]ria[,\s]*amostra isolada[:\s-]*([\d,.]+)\s*mcg/mg' # Novo padrão
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

            # Remove o símbolo ">" do valor da TFG/eRFG
            if key == 'TFG' and '>' in valor:
                valor = valor.replace('>', '').strip()  # Remove ">" e espaços em branco

            data[key] = valor

    # Código de depuração: Verifica se o TFG foi capturado corretamente
    #print(f"Valores extraídos: {data}")
    if 'TFG' in data:
        print(f"Valor do TFG extraído: {data['TFG']}")
    else:
        print("TFG não foi capturado.")

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

# Função para gerar uma análise
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
                dados_por_exame[nome].append(float(valor.replace(",", ".")))
            except ValueError:
                continue 
        
        for nome, valores in dados_por_exame.items():
            if len(valores) >= 2:
                tendencia = "estável"
                if valores[0] > valores[1]:
                    tendencia = "aumentando"
                elif valores[0] < valores[1]:
                    tendencia = "diminuindo"
                analise[nome] = f"O último exame de {nome} está {tendencia}. Último valor: {valores[0]}."
            else:
                analise[nome] = f"Apenas um valor disponível para {nome}. Último valor: {valores[0]}."
    
    return analise


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
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT data_coleta,
                         MAX(CASE WHEN nome = 'Creatinina' THEN valor ELSE NULL END) AS Creatinina,
                         MAX(CASE WHEN nome = 'TFG' THEN valor ELSE NULL END) AS TFG,
                         MAX(CASE WHEN nome = 'Ureia' THEN valor ELSE NULL END) AS Ureia,
                         MAX(CASE WHEN nome = 'Relação Proteína/Creatinina' THEN valor ELSE NULL END) AS "Relação Proteína/Creatinina",
                         MAX(CASE WHEN nome = 'Triglicerídeos' THEN valor ELSE NULL END) AS Triglicerídeos,
                         MAX(CASE WHEN nome = 'Colesterol LDL' THEN valor ELSE NULL END) AS LDL,
                         MAX(CASE WHEN nome = 'Ácido Úrico' THEN valor ELSE NULL END) AS "Ácido Úrico",
                         MAX(CASE WHEN nome = 'Sódio' THEN valor ELSE NULL END) AS Sódio,
                         MAX(CASE WHEN nome = 'Potássio' THEN valor ELSE NULL END) AS Potássio,
                         MAX(CASE WHEN nome = 'Cálcio' THEN valor ELSE NULL END) AS Cálcio,
                         MAX(CASE WHEN nome = 'Microalbuminúria' THEN valor ELSE NULL END) AS Microalbuminúria,
                         MAX(CASE WHEN nome = 'TFG' THEN valor ELSE NULL END) AS TFG  
                  FROM exames
                  GROUP BY data_coleta
                  ORDER BY substr(data_coleta, 7, 4) || '-' || 
                           substr(data_coleta, 4, 2) || '-' || 
                           substr(data_coleta, 1, 2) DESC''')
        rows = cursor.fetchall()
        
        exams_summary = []
        for row in rows:
            exams_summary.append({
                'data': row[0],
                'Creatinina': row[1] or '-',
                'TFG': row[2] or '-',
                'Ureia': row[3] or '-',
                'Relação Proteína/Creatinina': row[4] or '-',
                'Triglicerídeos': row[5] or '-',
                'LDL': row[6] or '-',
                'Ácido Úrico': row[7] or '-',
                'Sódio': row[8] or '-',
                'Potássio': row[9] or '-',
                'Cálcio': row[10] or '-',
                'Microalbuminúria': row[11] or '-'
            })

        # Depuração: Verifique as datas ordenadas
        print("Datas ordenadas:", [exam['data'] for exam in exams_summary])

    return render_template('exams_summary.html', exams_summary=exams_summary)

@app.route('/update_exams_summary', methods=['POST'])
def update_exams_summary():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        for i in range(1, len(request.form) // 10 + 1):
            data_coleta = request.form.get(f'data_{i}')
            values = {
                'Creatinina': request.form.get(f'creatinina_{i}'),
                'Ureia': request.form.get(f'ureia_{i}'),
                'Relação Proteína/Creatinina': request.form.get(f'relacao_{i}'),
                'Triglicerídeos': request.form.get(f'triglicerideos_{i}'),
                'Colesterol LDL': request.form.get(f'ldl_{i}'),
                'Ácido Úrico': request.form.get(f'acido_urico_{i}'),
                'Sódio': request.form.get(f'sodio_{i}'),
                'Potássio': request.form.get(f'potassio_{i}'),
                'Cálcio': request.form.get(f'calcio_{i}'),
                'Microalbuminúria': request.form.get(f'microalbuminuria_{i}') # Nova coluna  
            }
            for nome, valor in values.items():
                if valor and valor != '-':  # Verifica se o valor não é vazio ou '-'
                    # Verifica se já existe um registro
                    cursor.execute('SELECT id FROM exames WHERE nome = ? AND data_coleta = ?', (nome, data_coleta))
                    existing_record = cursor.fetchone()
                    if existing_record:
                        cursor.execute('UPDATE exames SET valor = ? WHERE nome = ? AND data_coleta = ?', (valor, nome, data_coleta))
                    else:
                        cursor.execute('INSERT INTO exames (nome, valor, data_coleta) VALUES (?, ?, ?)', (nome, valor, data_coleta))
        conn.commit()
    return redirect(url_for('exams_summary'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
