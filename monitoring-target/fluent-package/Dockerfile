FROM fluent/fluentd:latest

USER root


RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    ruby-dev \
    && gem install fluent-plugin-grafana-loki --no-document \
    && gem install fluent-plugin-record-modifier --no-document \
    && gem install fluent-plugin-parser-logfmt --no-document \
    && gem sources --clear-all \
    && apt-get purge -y --auto-remove \
                  -o APT::AutoRemove::RecommendsImportant=false \
                  build-essential \
                  ruby-dev \
    && rm -rf /var/lib/apt/lists/*

USER fluent