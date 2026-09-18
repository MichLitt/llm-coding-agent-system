def accept(events):
    seen = set(); output = []
    for event in events:
        if event[0] not in seen: seen.add(event[0]); output.append(event)
    return output
