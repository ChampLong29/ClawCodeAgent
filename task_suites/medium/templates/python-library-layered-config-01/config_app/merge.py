def deep_merge(base, overlay):
    result = dict(base)
    result.update(overlay)
    return result
