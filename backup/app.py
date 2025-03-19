from flask import Flask, render_template, request, redirect, url_for
import os
import pytesseract
from PyPDF2 import PdfReader
import re
import sqlite3

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

@app.route('/')
def index():
    return redirect(url_for('home'))

@app.route('/home')
def home():
    return render_template('home.html')

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
        return redirect(url_for('view_exams'))

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

# Função para extrair dados específicos dos exames
def extract_exam_data(text):
    data = {}
    
    # Padrão para extrair a data de emissão
    data_emissao_pattern = r'emissão[:]?\s*(\d{2}/\d{2}/\d{4})'
    data_emissao_match = re.search(data_emissao_pattern, text, re.IGNORECASE)
    
    if data_emissao_match:
        data['data_coleta'] = data_emissao_match.group(1)  # Extrai a data de emissão
    else:
        data['data_coleta'] = None  # Caso a data não seja encontrada


    patterns = {
        'Creatinina': r'creatinina[:\s]*([\d,.]+)\s*mg/dl',
        'Ureia': r'ure[ií]a[:\s]*([\d,.]+)\s*mg/dl',
        'TFG': r'filtra[çc][ãa]o glomerular[:\s>]*([\d,.]+)\s*ml/min',
        'Colesterol LDL': r'ldl[-\s]colesterol[:\s]*([\d,.]+)\s*mg/dl',
        'Triglicerídeos': r'triglicer[ií]des[:\s]*([\d,.]+)\s*mg/dl',
        'Sódio': r's[oó]dio[:\s]*([\d,.]+)\s*meq/l',
        'Potássio': r'pot[áa]ssio[:\s]*([\d,.]+)\s*meq/l',
        'Cálcio': r'c[aá]lcio[:\s]*([\d,.]+)\s*mg/dl',
        'Ácido Úrico': r'[áa]cido [úu]rico[:\s]*([\d,.]+)\s*mg/dl'
    }

    for key, pattern in patterns.items():
        matches = re.findall(pattern, text)
        if matches:
            data[key] = matches[-1]  # Pega o último valor encontrado

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

@app.route('/exams')
def view_exams():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT nome, valor, data_coleta FROM exames')
        exams = cursor.fetchall()

    return render_template('exams.html', exams=exams)

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
