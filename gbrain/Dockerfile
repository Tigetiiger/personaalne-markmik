# gbrain for Märkmik: one `gbrain serve --http` indexing the app's brain clone
# (docs/spikes/gbrain.md). Compose service: gbrain/compose.gbrain.yml.
FROM oven/bun:1.4-debian
# gbrain sync shells out to git
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# The commit verified in spike S1 (gbrain 0.54.1.0). The npm package named "gbrain" is another project.
ARG GBRAIN_REF=6040075
USER bun
# Globals where user bun can write them: the image links global bins into root-owned
# /usr/local/bin (BUN_INSTALL_BIN), and XDG_CACHE_HOME would move BUN_INSTALL.
ENV BUN_INSTALL=/home/bun/.bun BUN_INSTALL_BIN=/home/bun/.bun/bin
RUN bun install -g "github:garrytan/gbrain#${GBRAIN_REF}"
ENV PATH="/home/bun/.bun/bin:${PATH}" \
    GBRAIN_HOME=/data BRAIN_DIR=/brain \
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/brain
COPY --chmod=755 entrypoint.sh /entrypoint.sh
EXPOSE 8083
ENTRYPOINT ["/entrypoint.sh"]
