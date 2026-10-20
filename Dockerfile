# Dockerfile para Google Cloud Run / Produção
FROM python:3.11-slim

# Evita buffering para logs em tempo real no Cloud Logging
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    REQEMP_HOSTS=*

WORKDIR /app

# Dependências de sistema para compilação se necessário
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Instala dependências Python em camada separada para aproveitar cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o código-fonte da aplicação
COPY . .

# Garante a existência dos diretórios de saída e anexos
RUN mkdir -p saida anexos dados dados/nc dados/ne

EXPOSE 8080

# Inicia o Uvicorn ouvindo na porta informada pelo Cloud Run ($PORT)
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
