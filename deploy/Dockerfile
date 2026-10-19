# Образи шлюзу Gemini. Збірка — з теки deploy/:  docker compose build
# (контекст збірки — корінь репозиторію, бо звідти беруться пакети; під час роботи нічого з хоста, крім deploy/, не потрібно).
#
#   gateway — сам шлюз, без браузера (невеликий);
#   admin   — те саме + Chromium для входу в акаунти: `docker compose run --rm vault-admin login-console 1`.

FROM python:3.12-slim AS gateway
WORKDIR /app
COPY packages/ /app/packages/

# Звичайний користувач; тека даних належить йому, тож іменований том отримує правильного власника.
RUN useradd --uid 1000 --create-home gemini && mkdir /data && chown gemini:gemini /data

# Код апстріму gemini-web2api завантажується під час збірки (потрібен доступ до GitHub).
ENV XDG_CACHE_HOME=/opt/cache
RUN pip install --no-cache-dir /app/packages/gemini-vault /app/packages/gemini-web2api-manager \
 && gemini-web2api-manager fetch-upstream \
 && chmod -R a+rX /opt/cache

ENV GEMINI_MANAGER_HOME=/data
VOLUME /data
WORKDIR /data
USER gemini
EXPOSE 8080
STOPSIGNAL SIGTERM
# Налаштування шлюзу — змінні середовища (див. deploy/README.md): GEMINI_MANAGER_KEY, GEMINI_GATEWAY_API_KEYS, …
ENV GEMINI_GATEWAY_ALLOW_EMPTY=1
CMD ["gemini-web2api-manager", "serve", "--host", "0.0.0.0", "--port", "8080"]


FROM gateway AS admin
USER root
ENV PATCHRIGHT_BROWSERS_PATH=/opt/pw GEMINI_VAULT_NO_SANDBOX=1
RUN pip install --no-cache-dir "/app/packages/gemini-vault[login]" \
 && gemini-vault install-browser --with-deps \
 && chmod -R a+rX /opt/pw \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /data
USER gemini
ENTRYPOINT ["gemini-vault"]
CMD ["--help"]
