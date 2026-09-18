def parse(value):
    return [item for item in value.replace(";", ",").split(",") if item]
