# Usa uma imagem Python oficial (Linux Debian)
FROM python:3.11-slim

# Define o diretório de trabalho
WORKDIR /app

# Instala as dependências do sistema (Linux) necessárias para o dlib/face_recognition
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgtk-3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copia os arquivos do projeto para o container
COPY . .

# Instala as dependências do Python
RUN pip install --no-cache-dir -r requirements.txt

# Expõe a porta 8080 (Padrão do Railway)
EXPOSE 8080

# Comando para iniciar o site
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080"]