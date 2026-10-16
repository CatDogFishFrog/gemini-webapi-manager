# Шлюз gemini-web2api з автоперемиканням акаунтів. Збірка з кореня репозиторію:
#   docker build -f deploy/Dockerfile -t gemini-gateway .
# Браузера в образі немає: vault.bin з готовими сесіями створюється поза контейнером (див. deploy/README.md).
FROM python:3.12-slim
WORKDIR /app
COPY packages/ /app/packages/

# Код апстріму gemini-web2api завантажується під час збірки (потрібен доступ до GitHub) у спільний кеш,
# який читає будь-який користувач: контейнер запускається під довільним uid.
ENV XDG_CACHE_HOME=/opt/cache HOME=/tmp
RUN pip install --no-cache-dir /app/packages/gemini-vault /app/packages/gemini-web2api-manager \
 && gemini-web2api-manager fetch-upstream \
 && chmod -R a+rX /opt/cache

ENV GEMINI_MANAGER_HOME=/data
VOLUME /data
EXPOSE 8080
STOPSIGNAL SIGTERM
# Ключ шифрування (GEMINI_MANAGER_KEY) передається під час запуску, в образ його не вшито.
# Конфіг монтується лише для читання; --host/--port перекривають значення з нього.
CMD ["gemini-web2api-manager", "serve", "--host", "0.0.0.0", "--port", "8080", "--config", "/config/gateway.config.json"]
