FROM python:3.12-slim

# Hace que los mensajes de Python se muestren al instante en los logs
# (sin esto, a veces se "pierden" mensajes si el proceso se corta de golpe)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instala el navegador Chromium y todo lo que necesita para correr en el servidor
RUN playwright install --with-deps chromium

COPY monitor.py .

CMD ["python", "monitor.py"]
