# messy_utils.py — poorly structured with duplicated code

def process_list_a(items):
    result = []
    for item in items:
        if item > 0:
            result.append(item * 2)
    total = 0
    for x in result:
        total += x
    return total

def process_list_b(items):
    result = []
    for item in items:
        if item > 0:
            result.append(item * 2)
    total = 0
    for x in result:
        total += x
    avg = total / len(result) if result else 0
    return avg

def process_list_c(items, multiplier):
    result = []
    for item in items:
        if item > 0:
            result.append(item * multiplier)
    total = 0
    for x in result:
        total += x
    return total

def format_result(value, label):
    if value > 1000:
        return f"{label}: {value:.2f} (large)"
    elif value > 100:
        return f"{label}: {value:.2f} (medium)"
    else:
        return f"{label}: {value:.2f} (small)"

def format_metric(value, name):
    if value > 1000:
        return f"{name}: {value:.2f} (large)"
    elif value > 100:
        return f"{name}: {value:.2f} (medium)"
    else:
        return f"{name}: {value:.2f} (small)"
