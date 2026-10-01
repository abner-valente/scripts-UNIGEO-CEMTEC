"""GIF do mapa no tempo: quais paradas entram e o carimbo de cada quadro."""
import io

import numpy as np
import pandas as pd
import pytest
from PIL import Image, ImageSequence

from app import animacao


def png(cor=(120, 160, 200), tamanho=(400, 300)) -> bytes:
    arquivo = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(arquivo, format="PNG")
    return arquivo.getvalue()


def quadros_do_gif(gif: bytes) -> list[Image.Image]:
    return [quadro.convert("RGB") for quadro in ImageSequence.Iterator(Image.open(io.BytesIO(gif)))]


# =====================================================
# QUAIS PARADAS ENTRAM
# =====================================================
def test_cabendo_no_teto_entram_todas():
    assert animacao.passos(list(range(10)), maximo=72) == list(range(10))


def test_passando_do_teto_o_gif_pula_de_tantas_em_tantas():
    """Uma semana hora a hora dá 168 quadros: ~11 MB e um minuto de espera."""
    escolhidas = animacao.passos(list(range(168)), maximo=72)

    assert len(escolhidas) <= 72
    assert escolhidas[0] == 0                      # começa no início da janela
    saltos = np.diff(escolhidas)
    assert len(set(saltos)) == 1                   # o intervalo é constante, senão o GIF mente
    assert animacao.intervalo(list(range(168)), escolhidas) == saltos[0]


def test_sem_paradas_o_intervalo_nao_divide_por_zero():
    assert animacao.intervalo([], []) == 1


def test_no_diario_o_carimbo_traz_so_a_data():
    """Escrever "00:00" num mapa que resume o dia inteiro faria pensar que o valor é da meia-noite."""
    momento = pd.Timestamp("2026-09-24 00:00", tz="America/Campo_Grande")

    assert animacao.carimbo(momento, por_dia=True) == "24/09/2026"
    assert animacao.carimbo(momento, por_dia=False) == "24/09/2026 00:00"


# =====================================================
# O GIF
# =====================================================
def test_o_gif_tem_um_quadro_para_cada_imagem():
    gif = animacao.montar([(png(), "24/09/2026 15:00"), (png(), "24/09/2026 16:00"),
                           (png(), "24/09/2026 17:00")])

    assert gif[:6] in (b"GIF87a", b"GIF89a")
    assert len(quadros_do_gif(gif)) == 3


def test_cada_quadro_leva_o_seu_instante_escrito():
    """Fora do painel o GIF vira arquivo solto: sem o carimbo ninguém sabe de quando ele é."""
    liso = png()
    gif = animacao.montar([(liso, "24/09/2026 15:00")])

    quadro = np.array(quadros_do_gif(gif)[0])
    alto_a_esquerda = quadro[:60, :200]
    assert alto_a_esquerda.min() < 100          # tem texto escuro onde a imagem era uniforme
    assert alto_a_esquerda.max() > 200          # sobre a tarja clara
    antes = np.array(Image.open(io.BytesIO(liso)))[:60, :200].reshape(-1, 3)
    assert len(np.unique(antes, axis=0)) == 1  # antes o canto era de uma cor só


def test_o_tempo_de_cada_quadro_vai_no_arquivo():
    gif = Image.open(io.BytesIO(animacao.montar([(png(), "a"), (png(), "b")], ms_por_quadro=300)))
    assert gif.info["duration"] == 300


def test_um_quadro_so_ainda_gera_gif():
    assert len(quadros_do_gif(animacao.montar([(png(), "24/09/2026")]))) == 1
