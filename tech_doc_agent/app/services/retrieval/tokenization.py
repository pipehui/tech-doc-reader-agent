import re


TOKEN_RE = re.compile(r"[A-Za-z0-9_+#.-]+|[\u4e00-\u9fff]+")
CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group(0)
        if is_cjk(token):
            tokens.extend(token)
            tokens.extend(
                token[index : index + 2]
                for index in range(max(len(token) - 1, 0))
            )
            continue

        lowered = token.lower()
        tokens.append(lowered)
        tokens.extend(part.lower() for part in CAMEL_RE.findall(token) if len(part) > 1)

    return [token for token in tokens if token]


def is_cjk(token: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in token)


__all__ = ["tokenize"]
