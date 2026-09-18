def resume(completed, items):
    done = set(completed)
    return [item for item in items if item not in done]
