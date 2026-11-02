"""Exercise the real two-level navigation without changing business assertions."""

from xhs_seller.ui_navigation import WORKSPACES


def navigate(app, page):
    group = next(group for group, pages in WORKSPACES.items() if page in pages)
    app.sidebar.radio(key="nav_workspace").set_value(group).run()
    if len(WORKSPACES[group]) > 1:
        app.sidebar.radio(key=f"nav_page_{group}").set_value(page).run()
    else:
        assert app.session_state[f"nav_page_{group}"] == page
    return app
