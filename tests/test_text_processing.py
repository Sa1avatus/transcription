"""Fast unit tests for text processing without loading ML models."""

import importlib
import sys
import types
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _import_with_lightweight_ml_stubs(module_name: str):
    sys.modules.setdefault("torch", types.SimpleNamespace(device=object))
    sys.modules.setdefault("httpx", types.SimpleNamespace())
    sys.modules.setdefault("llama_cpp", types.SimpleNamespace(Llama=object))
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_strip_timestamps_preserves_content():
    translation = _import_with_lightweight_ml_stubs("translation")
    raw = "[0.00s -> 1.20s] [vi] Xin chào\n\n[1.20s -> 2.00s] hello"
    assert translation._strip_timestamps(raw) == ["Xin chào", "hello"]


def test_base_translator_keeps_grouped_output():
    translation = _import_with_lightweight_ml_stubs("translation")

    class EchoTranslator(translation.BaseTranslator):
        def translate_text(self, text, src_lang, tgt_lang):
            return text.upper()

    assert EchoTranslator().translate_lines(["one", "two"], "vie_Latn", "eng_Latn") == ["ONE", "TWO"]


def test_split_chunks_keeps_lines_intact():
    llm = _import_with_lightweight_ml_stubs("llm")
    assert llm._split_chunks("one\ntwo\nthree\n", 8) == ["one\ntwo\n", "three\n"]
