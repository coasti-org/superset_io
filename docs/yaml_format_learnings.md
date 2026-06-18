


- die `_[int].yml` am Ende spielen keine Rolle. Sollten wir entfernen, die ändern sich auch gern mal.

# Charts
- `query_context` can be removed, is created dynamically after upload.


# Restoring Content

- After uploading, you might get inconsistent chart ids on your dashboards. this leads to errors like "content not found, it might have been deleted?"
    - this can be fixed by edititing the dashboard, creating a dummy element in a page, removing the dummy, and saving. this forces a refresh of the layout by uuid, and re-assigns the chart ids.
    - Hypothesis: uuids are what matters, in a parsing layer, we might just remove the id fields from the dashboards yaml.
