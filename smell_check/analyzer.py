"""Code analyzer — extract typed governance units from Python source.

Pure functions for AST analysis. I/O wrappers for file/repo handling.
Produces TypedUnit v0 objects with mother types, subtypes, witnesses,
and purity classification.

This is the analysis engine. It takes source code in and produces
typed receipted governance reports out.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

from .mother_types import (
    CONTRACT, CONSTRAINT, UNCERTAINTY, RELATION, WITNESS,
    make_typed_unit, make_witness, infer_subtype, _generate_id,
)
from .stamp import h


# ---------------------------------------------------------------------------
# Impurity signals — what makes a function impure
# ---------------------------------------------------------------------------

# Function calls that indicate I/O or side effects
IMPURE_CALLS = frozenset({
    # Filesystem
    "open", "read", "write", "readlines", "writelines",
    # OS / env
    "os.environ", "os.getenv", "os.environ.get",
    "os.system", "os.popen", "os.exec", "os.execv",
    # Subprocess
    "subprocess.run", "subprocess.call", "subprocess.Popen",
    "subprocess.check_output", "subprocess.check_call",
    # Network
    "requests.get", "requests.post", "requests.put", "requests.delete",
    "requests.patch", "requests.head", "requests.request",
    "httpx.get", "httpx.post", "httpx.AsyncClient",
    "urllib.request.urlopen", "urllib.urlopen",
    "socket.socket", "socket.connect",
    # Dangerous deserialization
    "pickle.load", "pickle.loads",
    "marshal.load", "marshal.loads",
    "yaml.load", "yaml.unsafe_load",
    # Code execution
    "eval", "exec", "compile",
    # Database
    "cursor.execute", "connection.execute",
    "session.execute", "session.query",
    # Print / logging (side effects)
    "print", "logging.info", "logging.debug", "logging.warning",
    "logging.error", "logging.critical", "logger.info", "logger.debug",
    "logger.warning", "logger.error",
    # Time (non-determinism)
    "time.time", "time.sleep", "datetime.now", "datetime.utcnow",
    # Random (non-determinism)
    "random.random", "random.randint", "random.choice",
    "random.shuffle", "random.sample",
})

# Attribute access patterns that indicate impurity
IMPURE_ATTR_PATTERNS = [
    re.compile(r"os\.environ"),
    re.compile(r"os\.getenv"),
    re.compile(r"sys\.(stdin|stdout|stderr)"),
    re.compile(r"pickle\.(load|loads|dump|dumps)"),
    re.compile(r"requests\.\w+"),
    re.compile(r"httpx\.\w+"),
    re.compile(r"subprocess\.\w+"),
]

# Global variable access patterns
GLOBAL_INDICATORS = frozenset({"global", "nonlocal"})


# ---------------------------------------------------------------------------
# AST analysis — pure functions
# ---------------------------------------------------------------------------

def extract_functions(source: str, filename: str = "<unknown>") -> list[dict[str, Any]]:
    """Parse Python source and extract function-level information. Pure.

    Returns a list of function descriptors with:
    - name, lineno, end_lineno, args, returns, docstring
    - decorators, is_async, is_method
    - calls (function calls made inside the body)
    - impurity_signals (specific I/O or side effect indicators)
    - is_pure (True if no impurity signals detected)
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    functions = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        func = _analyze_function(node, source, filename)
        functions.append(func)

    return functions


def _analyze_function(node: ast.FunctionDef | ast.AsyncFunctionDef, source: str, filename: str) -> dict[str, Any]:
    """Analyze a single function node. Pure."""
    # Basic info
    name = node.name
    lineno = node.lineno
    end_lineno = getattr(node, "end_lineno", lineno)
    is_async = isinstance(node, ast.AsyncFunctionDef)

    # Args
    args = []
    for arg in node.args.args:
        arg_name = arg.arg
        annotation = ast.get_source_segment(source, arg.annotation) if arg.annotation else None
        args.append({"name": arg_name, "annotation": annotation})

    # Return annotation
    returns = ast.get_source_segment(source, node.returns) if node.returns else None

    # Docstring
    docstring = ast.get_docstring(node) or ""

    # Decorators
    decorators = []
    for dec in node.decorator_list:
        decorators.append(ast.get_source_segment(source, dec) or "")

    # Is it a method (first arg is self/cls)?
    is_method = bool(args and args[0]["name"] in ("self", "cls"))

    # Analyze body for calls and impurity
    calls = []
    impurity_signals = []
    has_global = False
    has_yield = False

    # Track function-body imports (like Langflow's inline import of langchain_experimental)
    body_imports = []

    for child in ast.walk(node):
        # Function-body imports (inline imports)
        if isinstance(child, ast.ImportFrom) and child.module:
            body_imports.append({
                "module": child.module,
                "names": [a.name for a in child.names],
                "line": getattr(child, "lineno", lineno),
            })
            # Flag dangerous execution-capable imports
            dangerous_import_fragments = {"experimental", "repl", "exec", "eval", "unsafe", "dangerous"}
            if any(frag in child.module.lower() for frag in dangerous_import_fragments):
                impurity_signals.append({
                    "type": "dangerous_import",
                    "call": f"from {child.module} import {', '.join(a.name for a in child.names)}",
                    "line": getattr(child, "lineno", lineno),
                })
        if isinstance(child, ast.Import):
            for alias in child.names:
                body_imports.append({
                    "module": alias.name,
                    "names": [alias.name],
                    "line": getattr(child, "lineno", lineno),
                })

        # Function calls
        if isinstance(child, ast.Call):
            call_name = _get_call_name(child)
            if call_name:
                calls.append(call_name)
                if call_name in IMPURE_CALLS or any(p.match(call_name) for p in IMPURE_ATTR_PATTERNS):
                    impurity_signals.append({
                        "type": "impure_call",
                        "call": call_name,
                        "line": getattr(child, "lineno", lineno),
                    })

        # Global/nonlocal
        if isinstance(child, (ast.Global, ast.Nonlocal)):
            has_global = True
            names = child.names if hasattr(child, "names") else []
            impurity_signals.append({
                "type": "global_state",
                "names": names,
                "line": getattr(child, "lineno", lineno),
            })

        # Yield (generator — not necessarily impure but marks as stateful)
        if isinstance(child, (ast.Yield, ast.YieldFrom)):
            has_yield = True

        # Dangerous keyword arguments (e.g. allow_dangerous_code=True)
        _DANGEROUS_KWARG_NAMES = {"dangerous", "shell", "unsafe", "no_verify", "skip_verify", "trust_remote_code"}
        if isinstance(child, ast.keyword) and child.arg:
            if any(d in child.arg.lower() for d in _DANGEROUS_KWARG_NAMES):
                if isinstance(child.value, ast.Constant) and child.value.value is True:
                    impurity_signals.append({
                        "type": "dangerous_flag",
                        "call": f"{child.arg}=True",
                        "line": getattr(child, "lineno", lineno),
                    })

        # Dangerous dict keys (e.g. {"allow_dangerous_code": True})
        if isinstance(child, ast.Dict):
            for k, v in zip(child.keys, child.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    if any(d in k.value.lower() for d in _DANGEROUS_KWARG_NAMES):
                        if isinstance(v, ast.Constant) and v.value is True:
                            impurity_signals.append({
                                "type": "dangerous_flag",
                                "call": f"{k.value}=True",
                                "line": getattr(k, "lineno", lineno),
                            })

        # Attribute access to known impure modules
        if isinstance(child, ast.Attribute):
            attr_str = _get_attribute_string(child)
            if attr_str:
                for pattern in IMPURE_ATTR_PATTERNS:
                    if pattern.match(attr_str):
                        impurity_signals.append({
                            "type": "impure_attr",
                            "attr": attr_str,
                            "line": getattr(child, "lineno", lineno),
                        })
                        break

    is_pure = len(impurity_signals) == 0 and not has_global

    # --- Exception safety analysis ---
    exception_signals = []
    for child in ast.walk(node):
        if isinstance(child, ast.ExceptHandler):
            handler_line = getattr(child, "lineno", lineno)
            # Bare except (catches everything including SystemExit, KeyboardInterrupt)
            if child.type is None:
                exception_signals.append({
                    "type": "bare_except",
                    "line": handler_line,
                    "message": "Bare except catches all exceptions including SystemExit",
                })
            # except Exception: pass (silent swallow)
            elif child.type and _node_name(child.type) in ("Exception", "BaseException"):
                # Check if body is just 'pass' or empty
                if len(child.body) == 1 and isinstance(child.body[0], ast.Pass):
                    exception_signals.append({
                        "type": "silent_exception",
                        "line": handler_line,
                        "message": f"Silently swallows {_node_name(child.type)} — errors will be invisible",
                    })
                # Check if body is just 'continue'
                elif len(child.body) == 1 and isinstance(child.body[0], ast.Continue):
                    exception_signals.append({
                        "type": "silent_exception",
                        "line": handler_line,
                        "message": f"Silently continues past {_node_name(child.type)}",
                    })

    # Unguarded deserialize: json.loads/yaml.load without try/except
    _DESERIALIZE_CALLS = {"json.loads", "json.load", "yaml.load", "yaml.safe_load",
                          "pickle.loads", "pickle.load", "ast.literal_eval"}
    deserialize_calls = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            call_name = _get_call_name(child)
            if call_name in _DESERIALIZE_CALLS:
                # Check if this call is inside a try block
                in_try = False
                for parent in ast.walk(node):
                    if isinstance(parent, ast.Try):
                        for stmt in ast.walk(parent):
                            if stmt is child:
                                in_try = True
                                break
                if not in_try:
                    deserialize_calls.append({
                        "call": call_name,
                        "line": getattr(child, "lineno", lineno),
                    })

    if deserialize_calls:
        for dc in deserialize_calls:
            exception_signals.append({
                "type": "unguarded_deserialize",
                "line": dc["line"],
                "message": f"{dc['call']} called without try/except — parse errors will crash",
            })

    # --- Guard detection ---
    # Guards are if/raise patterns that validate inputs
    guards = []
    for child in ast.walk(node):
        if isinstance(child, ast.If):
            # Check if the if-body contains a raise
            for body_node in child.body:
                if isinstance(body_node, ast.Raise):
                    guard_line = getattr(child, "lineno", lineno)
                    # Try to get the condition text
                    cond_text = ast.get_source_segment(source, child.test) or ""
                    guards.append({
                        "type": "validation_guard",
                        "line": guard_line,
                        "condition": cond_text[:80],
                    })

    # --- Global state mutation detection ---
    # Track module-level assignments that could be mutated
    global_mutations = []
    if has_global:
        for child in ast.walk(node):
            if isinstance(child, ast.Global):
                for gname in child.names:
                    global_mutations.append({
                        "name": gname,
                        "line": getattr(child, "lineno", lineno),
                    })

    # Extract source text for the function
    source_lines = source.splitlines()
    func_source = "\n".join(source_lines[lineno - 1:end_lineno]) if end_lineno else ""

    return {
        "name": name,
        "filename": filename,
        "lineno": lineno,
        "end_lineno": end_lineno,
        "args": args,
        "returns": returns,
        "docstring": docstring,
        "decorators": decorators,
        "is_async": is_async,
        "is_method": is_method,
        "is_pure": is_pure,
        "has_global": has_global,
        "has_yield": has_yield,
        "calls": calls,
        "body_imports": body_imports,
        "impurity_signals": impurity_signals,
        "exception_signals": exception_signals,
        "guards": guards,
        "global_mutations": global_mutations,
        "source": func_source,
    }


def _node_name(node) -> str:
    """Get the name from a Name or Attribute node. Pure."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _get_call_name(node: ast.Call) -> str | None:
    """Extract the name of a function call. Pure."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return _get_attribute_string(func)
    return None


def _get_attribute_string(node: ast.Attribute) -> str | None:
    """Build dotted attribute string like 'os.environ.get'. Pure."""
    parts = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    parts.reverse()
    return ".".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Mother type assignment — pure
# ---------------------------------------------------------------------------

def classify_function(func: dict[str, Any]) -> dict[str, Any]:
    """Assign mother type, subtype, and governance signals to a function. Pure."""

    name = func["name"]
    docstring = func.get("docstring", "")
    is_pure = func["is_pure"]
    impurity_signals = func.get("impurity_signals", [])
    calls = func.get("calls", [])
    returns = func.get("returns", "")
    args = func.get("args", [])
    text = f"{name} {docstring} {returns or ''}"

    # Determine primary mother type
    if impurity_signals:
        # Impure function with CONTRACT claim of purity = CONSTRAINT violation
        if _claims_purity(func):
            mother_type = CONSTRAINT
            subtype = "impurity_boundary"
            violation = f"Function claims purity but has impure operations: {', '.join(s['call'] if 'call' in s else s.get('attr','?') for s in impurity_signals[:3])}"
        else:
            mother_type = RELATION
            subtype = infer_subtype(RELATION, text)
            violation = None
    elif _is_validator(func):
        mother_type = CONSTRAINT
        subtype = infer_subtype(CONSTRAINT, text)
        violation = None
    elif _is_contract(func):
        mother_type = CONTRACT
        subtype = infer_subtype(CONTRACT, text)
        violation = None
    elif _is_uncertain(func):
        mother_type = UNCERTAINTY
        subtype = infer_subtype(UNCERTAINTY, text)
        violation = None
    else:
        mother_type = CONTRACT
        subtype = infer_subtype(CONTRACT, text)
        violation = None

    # Build violations list
    violations = []
    if violation:
        violations.append({
            "type": "constraint_violation",
            "message": violation,
            "mother_type": mother_type,
            "function": name,
            "file": func.get("filename", ""),
            "line": func.get("lineno", 0),
        })

    # Check for dangerous patterns
    for sig in impurity_signals:
        call = sig.get("call", sig.get("attr", ""))
        if call in ("pickle.loads", "pickle.load", "eval", "exec"):
            violations.append({
                "type": "dangerous_deserialization" if "pickle" in call else "code_execution",
                "message": f"{call} permits arbitrary code execution",
                "mother_type": CONSTRAINT,
                "function": name,
                "file": func.get("filename", ""),
                "line": sig.get("line", func.get("lineno", 0)),
            })
        if "os.environ" in call or "os.getenv" in call:
            violations.append({
                "type": "hidden_impurity",
                "message": f"Reads environment variable via {call} — output depends on deployment context",
                "mother_type": CONTRACT,
                "function": name,
                "file": func.get("filename", ""),
                "line": sig.get("line", func.get("lineno", 0)),
            })
        if sig.get("type") == "dangerous_flag":
            violations.append({
                "type": "dangerous_code_enabled",
                "message": f"Dangerous execution explicitly enabled: {call} — untrusted input may reach code execution",
                "mother_type": CONSTRAINT,
                "function": name,
                "file": func.get("filename", ""),
                "line": sig.get("line", func.get("lineno", 0)),
            })

    # Apparent vs actual contract
    apparent_contract = _infer_apparent_contract(func)
    actual_capabilities = _infer_actual_capabilities(func)
    contract_mismatch = apparent_contract and actual_capabilities and (
        any("execute" in c.lower() or "dangerous" in c.lower() or "code execution" in c.lower()
            for c in actual_capabilities)
        and "execute" not in apparent_contract.lower()
        and "dangerous" not in apparent_contract.lower()
    )
    if contract_mismatch:
        violations.append({
            "type": "contract_mismatch",
            "message": f"Contract mismatch: appears to '{apparent_contract}' but actually capable of: {', '.join(actual_capabilities)}",
            "mother_type": CONTRACT,
            "function": name,
            "file": func.get("filename", ""),
            "line": func.get("lineno", 0),
        })

    # Dependency path (trace dangerous imports through calls)
    dep_path = _trace_dependency_path(func)

    return {
        **func,
        "mother_type": mother_type,
        "subtype": subtype,
        "violations": violations,
        "purity_status": "pure" if is_pure else "impure",
        "apparent_contract": apparent_contract,
        "actual_capabilities": actual_capabilities,
        "dependency_path": dep_path,
    }


def _claims_purity(func: dict[str, Any]) -> bool:
    """Heuristic: does this function claim to be pure? Pure."""
    doc = func.get("docstring", "").lower()
    name = func["name"].lower()
    decorators = [d.lower() for d in func.get("decorators", [])]

    purity_signals = ["pure", "no side effect", "no io", "deterministic", "no i/o", "same input"]
    if any(s in doc for s in purity_signals):
        return True
    if any(s in name for s in ["pure", "compute", "calculate", "hash", "validate"]):
        return True
    if any("pure" in d or "staticmethod" in d for d in decorators):
        return True
    return False


def _is_validator(func: dict[str, Any]) -> bool:
    """Heuristic: is this a validation/constraint function? Pure."""
    name = func["name"].lower()
    doc = func.get("docstring", "").lower()
    signals = ["validate", "check", "verify", "assert", "ensure", "require", "guard", "constrain"]
    return any(s in name or s in doc for s in signals)


def _is_contract(func: dict[str, Any]) -> bool:
    """Heuristic: is this a contract/promise function? Pure."""
    name = func["name"].lower()
    doc = func.get("docstring", "").lower()
    has_return = func.get("returns") is not None
    signals = ["get", "create", "build", "make", "generate", "produce", "return", "compute"]
    return has_return or any(s in name for s in signals)


def _infer_apparent_contract(func: dict[str, Any]) -> str:
    """Infer what this function claims to do from its name and docstring. Pure."""
    name = func["name"]
    doc = func.get("docstring", "")

    # Use first sentence of docstring if available
    if doc:
        first_sentence = doc.split(".")[0].strip()
        if len(first_sentence) > 10:
            return first_sentence

    # Fall back to name-based inference
    name_lower = name.lower()
    if "load" in name_lower:
        return "load and return data"
    if "validate" in name_lower or "check" in name_lower:
        return "validate input"
    if "predict" in name_lower or "infer" in name_lower:
        return "run prediction or inference"
    if "build" in name_lower or "create" in name_lower:
        return "build or create an object"
    if "get" in name_lower or "fetch" in name_lower:
        return "retrieve data"
    if "compute" in name_lower or "calculate" in name_lower:
        return "compute a result"
    if "log" in name_lower or "send" in name_lower:
        return "send or log data"
    return f"execute {name}"


def _infer_actual_capabilities(func: dict[str, Any]) -> list[str]:
    """Infer what this function actually does from its body analysis. Pure."""
    capabilities = []
    for sig in func.get("impurity_signals", []):
        call = sig.get("call", sig.get("attr", ""))
        if "pickle" in call:
            capabilities.append("arbitrary code execution via deserialization")
        elif "eval" == call or "exec" == call:
            capabilities.append("arbitrary code execution")
        elif "subprocess" in call:
            capabilities.append("OS command execution")
        elif "os.environ" in call or "os.getenv" in call:
            capabilities.append("environment-dependent behavior")
        elif "requests" in call or "httpx" in call or "urllib" in call:
            capabilities.append("network I/O")
        elif "open" == call:
            capabilities.append("file I/O")
        elif sig.get("type") == "dangerous_flag":
            capabilities.append(f"dangerous execution enabled ({call})")
        elif sig.get("type") == "dangerous_import":
            capabilities.append(f"imports execution-capable module ({call})")

    # Deduplicate
    return list(dict.fromkeys(capabilities))


def _trace_dependency_path(func: dict[str, Any]) -> list[str]:
    """Trace the dependency path from this function to dangerous capabilities. Pure."""
    path = []

    # Start with the function itself
    name = func["name"]
    filename = func.get("filename", "")
    if filename:
        path.append(f"{filename}:{name}")
    else:
        path.append(name)

    # Body imports → what dangerous modules are pulled in
    for imp in func.get("body_imports", []):
        module = imp.get("module", "")
        names = imp.get("names", [])
        if module:
            path.append(f"{module}.{', '.join(names)}" if names else module)

    # Dangerous signals → what capability is reached
    for sig in func.get("impurity_signals", []):
        if sig.get("type") == "dangerous_flag":
            path.append(f"→ {sig['call']} (execution boundary)")
        elif sig.get("type") == "dangerous_import":
            path.append(f"→ {sig['call']} (execution-capable import)")
        elif "pickle" in sig.get("call", ""):
            path.append(f"→ {sig['call']} (deserialization execution)")
        elif sig.get("call") in ("eval", "exec"):
            path.append(f"→ {sig['call']} (direct code execution)")

    return path


def _is_uncertain(func: dict[str, Any]) -> bool:
    """Heuristic: does this function express uncertainty? Pure."""
    doc = func.get("docstring", "").lower()
    name = func["name"].lower()
    signals = ["try", "maybe", "attempt", "guess", "heuristic", "approximate", "fallback", "default"]
    return any(s in name or s in doc for s in signals)


# ---------------------------------------------------------------------------
# Dependency analysis — pure
# ---------------------------------------------------------------------------

def extract_imports(source: str, filename: str = "<unknown>") -> list[dict[str, Any]]:
    """Extract import statements from Python source. Pure."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({
                    "module": alias.name,
                    "alias": alias.asname,
                    "type": "import",
                    "line": node.lineno,
                    "filename": filename,
                })
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append({
                    "module": f"{module}.{alias.name}" if module else alias.name,
                    "alias": alias.asname,
                    "from_module": module,
                    "name": alias.name,
                    "type": "from_import",
                    "line": node.lineno,
                    "filename": filename,
                })

    return imports


def classify_dependency(imp: dict[str, Any], repo_modules: set[str]) -> dict[str, Any]:
    """Classify a dependency as in-repo (stamped) or external (unstamped). Pure."""
    module = imp.get("module", "")
    top_level = module.split(".")[0]

    # Standard library
    stdlib = _is_stdlib(top_level)

    # In-repo
    in_repo = top_level in repo_modules or module in repo_modules

    # Dangerous modules
    dangerous = top_level in ("pickle", "marshal", "yaml", "subprocess", "os", "eval")

    return {
        **imp,
        "is_stdlib": stdlib,
        "is_in_repo": in_repo,
        "is_external": not stdlib and not in_repo,
        "is_dangerous": dangerous,
        "provenance": "stamped" if in_repo else ("stdlib" if stdlib else "unstamped"),
    }


def _is_stdlib(module_name: str) -> bool:
    """Check if a module is part of the Python standard library. Pure (heuristic)."""
    # Common stdlib modules — not exhaustive but covers the important ones
    stdlib_modules = frozenset({
        "__future__", "abc", "argparse", "ast", "asyncio", "base64", "collections", "configparser",
        "contextlib", "copy", "csv", "dataclasses", "datetime", "decimal", "difflib",
        "enum", "errno", "fileinput", "fnmatch", "fractions", "functools", "gc",
        "getpass", "glob", "gzip", "hashlib", "heapq", "hmac", "html", "http",
        "importlib", "inspect", "io", "itertools", "json", "keyword", "linecache",
        "locale", "logging", "math", "mimetypes", "operator", "os", "pathlib",
        "pickle", "platform", "pprint", "queue", "random", "re", "secrets",
        "shutil", "signal", "socket", "sqlite3", "ssl", "stat", "string",
        "struct", "subprocess", "sys", "tempfile", "textwrap", "threading",
        "time", "timeit", "token", "tokenize", "traceback", "types", "typing",
        "unicodedata", "unittest", "urllib", "uuid", "warnings", "weakref",
        "xml", "zipfile", "zlib",
    })
    return module_name in stdlib_modules


# ---------------------------------------------------------------------------
# Full analysis — combines function + dependency analysis
# ---------------------------------------------------------------------------

def analyze_source(source: str, filename: str = "<unknown>", repo_modules: set[str] | None = None) -> dict[str, Any]:
    """Analyze a single Python source file. Pure.

    Returns a complete analysis with typed functions, classified dependencies,
    violations, and summary statistics.
    """
    repo_modules = repo_modules or set()

    # Extract and classify functions
    raw_functions = extract_functions(source, filename)
    functions = [classify_function(f) for f in raw_functions]

    # Extract and classify imports
    raw_imports = extract_imports(source, filename)
    dependencies = [classify_dependency(imp, repo_modules) for imp in raw_imports]

    # Collect all violations
    violations = []
    for f in functions:
        violations.extend(f.get("violations", []))

    # Module-level env var reads — these taint all functions that use the constants
    module_env_reads = []
    try:
        tree = ast.parse(source, filename=filename)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_name = _get_call_name(node)
                if call_name and ("os.environ" in call_name or "os.getenv" in call_name):
                    # Check if this is at module level (not inside a function)
                    module_env_reads.append({
                        "type": "module_level_env",
                        "message": f"Module reads environment variable via {call_name} at load time — all functions using this value depend on deployment context",
                        "mother_type": CONTRACT,
                        "function": None,
                        "file": filename,
                        "line": getattr(node, "lineno", 0),
                    })
    except SyntaxError:
        pass

    violations.extend(module_env_reads)

    # Unstamped external dependencies are provenance gaps
    for dep in dependencies:
        if dep["is_external"] and not dep["is_stdlib"]:
            violations.append({
                "type": "provenance_gap",
                "message": f"External dependency '{dep['module']}' has no provenance receipt",
                "mother_type": WITNESS,
                "function": None,
                "file": filename,
                "line": dep.get("line", 0),
            })

    # Semantic pattern detection — trust boundary violations
    # These catch issues that aren't about impurity but about wrong trust
    for f in functions:
        doc = f.get("docstring", "").lower()
        name_lower = f["name"].lower()
        source = f.get("source", "").lower()

        # Pattern: function discards or ignores security-critical data
        discard_signals = ["silently discard", "never used", "not used", "dropped",
                          "ignored", "skip", "without verif", "never verif",
                          "not verif", "no signature", "no crypto"]
        for signal in discard_signals:
            if signal in doc or signal in source:
                violations.append({
                    "type": "trust_boundary_violation",
                    "message": f"Function '{f['name']}' may discard or skip security-critical verification ({signal})",
                    "mother_type": WITNESS,
                    "function": f["name"],
                    "file": filename,
                    "line": f.get("lineno", 0),
                })
                break

        # Pattern: untrusted input steers verification logic
        untrusted_steering = ["header.get", "token.get", "input.get", "request.get",
                             "untrusted", "attacker", "controlled"]
        verify_context = "verify" in name_lower or "valid" in name_lower or "auth" in name_lower
        if verify_context:
            for signal in untrusted_steering:
                if signal in source:
                    violations.append({
                        "type": "trust_boundary_violation",
                        "message": f"Verification function '{f['name']}' may use untrusted input to steer verification logic ({signal})",
                        "mother_type": CONSTRAINT,
                        "function": f["name"],
                        "file": filename,
                        "line": f.get("lineno", 0),
                    })
                    break

        # Pattern: function named 'verify' or 'validate' that returns without crypto check
        if verify_context and f["is_pure"]:
            calls = set(f.get("calls", []))
            crypto_calls = {"hmac.compare_digest", "hmac.new", "verify_signature",
                           "check_signature", "rsa.verify", "ecdsa.verify",
                           "hashlib.sha256", "verify"}
            if not calls & crypto_calls and len(f.get("args", [])) > 0:
                # A verify function that doesn't call any crypto — suspicious
                if any(kw in doc for kw in ["verify", "signature", "token", "jwt", "authenticate"]):
                    violations.append({
                        "type": "missing_crypto_verification",
                        "message": f"Function '{f['name']}' appears to validate credentials but performs no cryptographic verification",
                        "mother_type": WITNESS,
                        "function": f["name"],
                        "file": filename,
                        "line": f.get("lineno", 0),
                    })

    # Deduplicate violations by (function, line, type)
    seen = set()
    deduped_violations = []
    for v in violations:
        key = (v.get("function"), v.get("line"), v.get("type"), v.get("message", "")[:50])
        if key not in seen:
            seen.add(key)
            deduped_violations.append(v)
    violations = deduped_violations

    # Summary
    type_counts = {}
    for f in functions:
        mt = f.get("mother_type", "?")
        type_counts[mt] = type_counts.get(mt, 0) + 1

    pure_count = sum(1 for f in functions if f["is_pure"])
    impure_count = len(functions) - pure_count

    return {
        "filename": filename,
        "functions": functions,
        "dependencies": dependencies,
        "violations": violations,
        "summary": {
            "total_functions": len(functions),
            "pure_count": pure_count,
            "impure_count": impure_count,
            "type_counts": type_counts,
            "violation_count": len(violations),
            "external_deps": sum(1 for d in dependencies if d["is_external"]),
            "unstamped_deps": sum(1 for d in dependencies if d["provenance"] == "unstamped"),
        },
        "source_hash": h(source.encode()),
    }


# ---------------------------------------------------------------------------
# Multi-file analysis
# ---------------------------------------------------------------------------

def analyze_repo(files: dict[str, str]) -> dict[str, Any]:
    """Analyze multiple Python source files as a repo. Pure.

    Args:
        files: dict of {filename: source_code}

    Returns complete repo analysis with per-file results, aggregate violations,
    and dependency graph.
    """
    # Discover repo module names (for stamped vs unstamped classification)
    repo_modules = set()
    for filename in files:
        # Convert file path to module name
        module = Path(filename).stem
        repo_modules.add(module)
        # Also add parent package names
        parts = Path(filename).parts
        for i in range(len(parts)):
            repo_modules.add(parts[i].replace(".py", ""))

    # Analyze each file
    file_results = {}
    all_violations = []
    all_functions = []
    all_dependencies = []

    for filename, source in files.items():
        result = analyze_source(source, filename, repo_modules)
        file_results[filename] = result
        all_violations.extend(result["violations"])
        all_functions.extend(result["functions"])
        all_dependencies.extend(result["dependencies"])

    # Aggregate type counts
    type_counts = {}
    for f in all_functions:
        mt = f.get("mother_type", "?")
        type_counts[mt] = type_counts.get(mt, 0) + 1

    pure_count = sum(1 for f in all_functions if f["is_pure"])

    # Unique external deps
    external_deps = set()
    for d in all_dependencies:
        if d["is_external"]:
            external_deps.add(d["module"].split(".")[0])

    return {
        "files": file_results,
        "violations": all_violations,
        "summary": {
            "total_files": len(files),
            "total_functions": len(all_functions),
            "pure_count": pure_count,
            "impure_count": len(all_functions) - pure_count,
            "type_counts": type_counts,
            "violation_count": len(all_violations),
            "external_deps": sorted(external_deps),
            "unstamped_deps": sum(1 for d in all_dependencies if d["provenance"] == "unstamped"),
        },
    }
