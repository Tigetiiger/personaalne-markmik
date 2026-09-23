"""All blueprints, one file per work package (docs/CONTRACTS.md, "Ownership"). Registered in
this order; the lead owns this list."""

from . import (ai, calendar, collab, connections, core, graph, images, inbox, notes_api, pages,
               related, settings, share)

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
]
