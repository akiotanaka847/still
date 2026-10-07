# ─── Etapa 1: el binario que reescribe PSD ───────────────────────────────────
# Rust solo hace falta para construirlo. La imagen final no lo lleva: se copia
# el ejecutable ya compilado, que son unos 450 KB. Si esta etapa fallara, el
# convertidor sigue funcionando por su camino de respaldo (ver a_psd) — peor
# PSD, pero la aplicacion arranca igual.
FROM rust:1-slim AS psd
WORKDIR /psd
COPY psd-nativo/Cargo.toml psd-nativo/Cargo.lock ./
COPY psd-nativo/vendor ./vendor
COPY psd-nativo/src ./src
RUN cargo build --release --locked

# ─── Etapa 2: la aplicacion ──────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# El binario va donde convertidor.py lo busca (junto al modulo, en
# psd-nativo/target/release). Se copia DESPUES del codigo para que el `COPY . .`
# de arriba no lo pise.
COPY --from=psd /psd/target/release/still-psd ./psd-nativo/target/release/still-psd

# DATA_DIR (sesiones, usuarios, secreto de cookies) va aparte del código para
# poder montar ahí un volumen persistente en plataformas de filesystem
# efímero (Railway) sin que un redeploy borre todo — ver main.py.
ENV DATA_DIR=/data
RUN mkdir -p /data

EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
