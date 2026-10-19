"""Cheap, local heuristic to decide if a message is code-related — no LLM
call, just a keyword/pattern scan. Good enough for routing; doesn't need to
be perfect, just needs to catch the obvious cases ("ayudame con este error
de python", "qué hace este código", a pasted stack trace, etc.)."""
import re

_CODE_WORDS = {
    "código", "codigo", "code", "función", "funcion", "function", "método", "metodo",
    "script", "bug", "error", "excepción", "excepcion", "exception", "stacktrace",
    "traceback", "compilar", "compile", "programar", "programación", "programacion",
    "programming", "debug", "debuggear", "variable", "clase", "class", "api",
    "endpoint", "git", "github", "repo", "repositorio", "commit", "branch",
    "pull request", "merge", "terminal", "consola", "console", "comando", "command",
    "servidor", "server", "base de datos", "database", "sql", "query", "deploy",
    "despliegue", "docker", "kubernetes", "librería", "libreria", "library",
    "paquete", "package", "dependencia", "dependency", "síntaxis", "sintaxis",
    "syntax", "algoritmo", "algorithm", "json", "yaml", "regex",
}

_CODE_PATTERNS = [
    r"```",  # markdown code fence
    r"\.(py|js|ts|jsx|tsx|java|go|rs|c|cpp|cs|rb|php|sh|json|yaml|yml|toml|sql|html|css)\b",
    r"\bdef \w+\(",
    r"\bfunction \w+\(",
    r"\bimport \w+",
    r"\bclass \w+",
    r"File \"[^\"]+\", line \d+",  # Python traceback
    r"Traceback \(most recent call last\)",
    r"\$\s*(npm|pip|git|docker|python|node)\b",
]


def is_code_related(text: str) -> bool:
    lowered = text.lower()
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in _CODE_PATTERNS):
        return True
    words = set(re.findall(r"[a-záéíóúñ]+", lowered))
    return bool(words & _CODE_WORDS)
