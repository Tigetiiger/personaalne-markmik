"""All blueprints, one file per work package (docs/CONTRACTS.md, "Ownership"). Registered in
this order; the lead owns this list."""

from . import (ai, arvuta, calendar, collab, connections, core, failid, graph, images, inbox, mallid, meedia,
               meedia_jaga, notes_api, pages, related, settings, share, stocks, stocks_ai, stocks_market, stocks_night,
               stocks_portfolio, stocks_sources)

BLUEPRINTS = [
    core.bp,        # lead: login, assets, editor files
    pages.bp,       # WP-C shell: /, /m/<slug>, /tana
    notes_api.bp,   # WP-B
    collab.bp,      # WP-A
    images.bp,      # WP-A
    calendar.bp,    # WP-D
    inbox.bp,       # WP-E2
    settings.bp,    # WP-E2
    connections.bp,  # WP-E2
    ai.bp,          # WP-F
    share.bp,       # WP-G
    related.bp,     # WP-H
    graph.bp,       # WP-I
    stocks.bp,      # lead: Aktsiad pages and settings
    stocks_market.bp,     # WP stocks-market
    stocks_portfolio.bp,  # WP stocks-portfolio
    stocks_sources.bp,    # WP stocks-sources
    stocks_ai.bp,         # WP stocks-ai
    stocks_night.bp,      # WP stocks-night
    meedia.bp,      # WP meedia
    meedia_jaga.bp,  # WP meedia: public share pages
    arvuta.bp,      # Python cells in notes (docs/ARVUTUS-PLAN.md)
    mallid.bp,      # templates (docs/ARVUTUS-PLAN.md)
    failid.bp,      # data files for Python cells (docs/FAILID-PLAN.md)
]
