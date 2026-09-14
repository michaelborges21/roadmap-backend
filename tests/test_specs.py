"""Rastreabilidade: specs/ não pode citar teste inexistente nem ter requisito funcional sem teste."""

import re
from pathlib import Path

RAIZ = Path(__file__).parent.parent
REF_TESTE = re.compile(r"(tests/[\w/]+\.py)::(test_\w+)")


def test_referencias_de_teste_nas_specs_existem():
    refs = {m.groups() for md in (RAIZ / "specs").rglob("*.md") for m in REF_TESTE.finditer(md.read_text())}
    assert refs, "nenhuma referência de teste em specs/"
    faltando = [
        f"{arquivo}::{teste}"
        for arquivo, teste in sorted(refs)
        if not (RAIZ / arquivo).exists() or f"def {teste}(" not in (RAIZ / arquivo).read_text()
    ]
    assert not faltando


def test_todo_requisito_funcional_tem_teste():
    linhas = (RAIZ / "specs" / "requisitos.md").read_text().splitlines()
    sem_teste = [l.split("|")[1].strip() for l in linhas if l.startswith("| RF-") and not REF_TESTE.search(l)]
    assert not sem_teste
