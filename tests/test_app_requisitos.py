"""Na nuvem, o painel só tem o que está em `app/requirements.txt`.

Este teste existe por causa de um app fora do ar: a aba de risco importa a regra do produto, o
produto carrega `modulos/excel.py` junto, e o `openpyxl` não estava declarado. Na máquina de
quem programa nada disso aparece — lá o venv tem as dependências dos dois lados.

A verificação roda num processo à parte porque o pytest já importou meio mundo antes: é preciso
ver o que os módulos do painel puxam **sozinhos**. E mesmo no processo novo conta só o que entrou
depois do import do painel: o Python carrega coisas ao iniciar, antes de qualquer linha nossa.
No 3.10 o setuptools vem instalado e tem um `.pth` que se importa a cada inicialização — o teste
acusava `setuptools` como dependência do painel, e passou de 30/09 a 01/10/2026 vermelho na CI
por isso, sem nenhuma dependência faltando de verdade.
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
REQUISITOS = RAIZ / "app" / "requirements.txt"

# Os módulos do painel que a tela importa. O explorador.py fica de fora: importá-lo executa a
# página inteira, que precisa do Streamlit rodando.
MODULOS = ["animacao", "chuva", "dados", "previsao", "qualidade", "risco", "superficie", "variaveis"]

ESPIAO = f"""
import sys
sys.path.insert(0, {str(RAIZ)!r})
antes = set(sys.modules)
from app import {", ".join(MODULOS)}
novos = set(sys.modules) - antes
import importlib.metadata as meta
mapa = meta.packages_distributions()
carregados = {{nome.split(".")[0] for nome in novos}}
pacotes = {{dist for nome in carregados if nome not in sys.stdlib_module_names
           for dist in mapa.get(nome, [])}}
print("\\n".join(sorted(pacotes)))
"""


def normalizar(nome: str) -> str:
    return re.sub(r"[-_.]+", "-", nome).lower()


def declarados() -> set[str]:
    import importlib.metadata as meta

    nomes = set()
    for linha in REQUISITOS.read_text(encoding="utf-8").splitlines():
        limpa = linha.split("#")[0].strip()
        if not limpa:
            continue
        nome = normalizar(re.split(r"[<>=!~\s\[]", limpa)[0])
        nomes.add(nome)
        # "pacote[extra]" declara também o que o extra traz (o psycopg[binary] traz o psycopg-binary)
        pedidos = re.search(r"\[([^\]]*)\]", limpa)
        if pedidos:
            extras = {extra.strip() for extra in pedidos.group(1).split(",")}
            for exigencia in meta.requires(nome) or []:
                if any(f'extra == "{extra}"' in exigencia for extra in extras):
                    nomes.add(normalizar(re.split(r"[<>=!;\s\[]", exigencia.strip())[0]))
    return nomes


def com_as_dependencias(pacotes: set[str]) -> set[str]:
    import importlib.metadata as meta

    vistos, fila = set(), list(pacotes)
    while fila:
        atual = normalizar(fila.pop())
        if atual in vistos:
            continue
        vistos.add(atual)
        try:
            exigencias = meta.requires(atual) or []
        except meta.PackageNotFoundError:
            continue
        for exigencia in exigencias:
            if "extra ==" in exigencia:      # só vem se alguém pedir o extra; não vem sozinho
                continue
            fila.append(re.split(r"[<>=!;\s\[]", exigencia.strip())[0])
    return vistos


def test_o_painel_declara_tudo_o_que_seus_modulos_importam():
    espiao = subprocess.run([sys.executable, "-c", ESPIAO], capture_output=True, text=True)
    assert espiao.returncode == 0, espiao.stderr

    carregados = {normalizar(linha) for linha in espiao.stdout.split() if linha}
    cobertos = com_as_dependencias(declarados())
    faltando = sorted(carregados - cobertos)

    assert not faltando, (f"o painel importa {faltando} e não os declara em app/requirements.txt "
                          "— na nuvem isso derruba a página inteira")


def test_a_regra_do_risco_arrasta_o_escritor_de_planilha():
    assert "openpyxl" in declarados()


def test_todo_cache_resource_tem_teto_de_entradas():
    arvore = ast.parse((RAIZ / "app" / "explorador.py").read_text(encoding="utf-8"))

    sem_teto = [
        no.name
        for no in ast.walk(arvore) if isinstance(no, ast.FunctionDef)
        for enfeite in no.decorator_list
        if isinstance(enfeite, ast.Call) and ast.unparse(enfeite.func).endswith("cache_resource")
        and not any(chave.arg == "max_entries" for chave in enfeite.keywords)
    ]

    assert not sem_teto, f"@st.cache_resource sem max_entries: {', '.join(sem_teto)}"
