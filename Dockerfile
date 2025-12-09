# Usa uma versão leve do Python 3.11
FROM python:3.11-slim

# Define o diretório de trabalho
WORKDIR /app

# 1. Instala dependências do sistema e o pacote de idiomas (locales)
RUN apt-get update && apt-get install -y \
    locales \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgtk-3-dev \
    && rm -rf /var/lib/apt/lists/*

# 2. Configura o idioma Português (Brasil) no Linux
RUN sed -i -e 's/# pt_BR.UTF-8 UTF-8/pt_BR.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen

# 3. Define as variáveis de ambiente para usar o Português
ENV LANG pt_BR.UTF-8
ENV LC_ALL pt_BR.UTF-8

# Copia os arquivos do projeto
COPY . .

# Instala as dependências do Python
RUN pip install --no-cache-dir -r requirements.txt

# Expõe a porta 8080
EXPOSE 8080

# Inicia o servidor
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080", "--timeout", "120"]