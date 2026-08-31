FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# DATA_DIR (sesiones, usuarios, secreto de cookies) va aparte del código para
# poder montar ahí un volumen persistente en plataformas de filesystem
# efímero (Railway) sin que un redeploy borre todo — ver main.py.
ENV DATA_DIR=/data
RUN mkdir -p /data

EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
