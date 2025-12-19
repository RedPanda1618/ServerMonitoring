# Specify the base image currently in use
FROM fluent/fluentd:latest

# Switch to the root user to install plugins
USER root

# Install fluent-plugin-loki
# The --no-document option skips installing documentation, reducing the image size
RUN fluent-gem install fluent-plugin-grafana-loki --no-document

# Switch back to the default fluent user
USER fluent