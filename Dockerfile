# Resta SaaS - Odoo 19 (slim Community + Enterprise + Resta & Basic Solution modules)
# Built by Dokploy from this repository. One image runs the web app and the backup job.
FROM ubuntu:24.04

ARG TARGETARCH=amd64
ARG WKHTMLTOPDF_VERSION=0.12.6.1-3
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    ODOO_ROOT=/opt/odoo \
    ODOO_RC=/etc/odoo/odoo.conf \
    TZ=Asia/Riyadh

# System packages: build deps for the Python wheels, PostgreSQL 16 client (pg_dump
# must match the server), Arabic fonts for PDF reports, rclone for off-site backups.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev build-essential \
        libpq-dev libldap2-dev libsasl2-dev libxml2-dev libxslt1-dev \
        libjpeg-dev zlib1g-dev libffi-dev libssl-dev \
        postgresql-client-16 curl ca-certificates tzdata gnupg \
        fonts-noto-core fonts-noto-ui-core fonts-kacst fonts-dejavu-core fontconfig \
        xfonts-75dpi xfonts-base nodejs npm rclone \
    && curl -fsSL -o /tmp/wkhtmltox.deb \
        "https://github.com/wkhtmltopdf/packaging/releases/download/${WKHTMLTOPDF_VERSION}/wkhtmltox_${WKHTMLTOPDF_VERSION}.jammy_${TARGETARCH}.deb" \
    && apt-get install -y --no-install-recommends /tmp/wkhtmltox.deb \
    && rm -f /tmp/wkhtmltox.deb \
    # rtlcss: needed by Odoo to build right-to-left (Arabic) stylesheets
    && npm install -g rtlcss@4 \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies first (cached while only the addons change)
COPY odoo/requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
    && pip install --no-cache-dir --upgrade pip wheel setuptools \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Odoo user and writable folders (named volumes inherit this ownership)
RUN useradd --system --create-home --home-dir /var/lib/odoo --shell /bin/bash --uid 1001 odoo \
    && mkdir -p /etc/odoo /var/lib/odoo /backups \
    && chown -R odoo:odoo /etc/odoo /var/lib/odoo

# Source code
COPY --chown=odoo:odoo odoo/ /opt/odoo/odoo/
COPY --chown=odoo:odoo enterprise/ /opt/odoo/enterprise/
COPY --chown=odoo:odoo resta_addons/ /opt/odoo/resta_addons/
COPY --chown=odoo:odoo bs_addons/ /opt/odoo/bs_addons/

# Scripts
COPY docker/entrypoint.sh /usr/local/bin/resta-entrypoint
COPY docker/resta-init.sh /usr/local/bin/resta-init
COPY docker/resta-backup.sh /usr/local/bin/resta-backup
COPY docker/resta-restore.sh /usr/local/bin/resta-restore
COPY docker/resta-upgrade.sh /usr/local/bin/resta-upgrade
RUN chmod 755 /usr/local/bin/resta-*

USER odoo
WORKDIR /var/lib/odoo
EXPOSE 8069 8072
VOLUME ["/var/lib/odoo"]

ENTRYPOINT ["resta-entrypoint"]
CMD ["odoo"]
