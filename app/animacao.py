"""GIF de um mapa ao longo do tempo: um quadro por hora, ou por dia, da janela escolhida.

O mapa parado responde "como estava naquela hora"; a sequência responde "por onde entrou" — a
frente que desce pelo sul, a mancha de chuva que atravessa o estado. É a mesma pergunta que o
deslizante responde, mas sem ninguém precisar ficar arrastando.

Cada quadro leva escrita a sua data e hora: fora do painel o GIF vira um arquivo solto, mandado
por e-mail ou colado numa apresentação, e sem o carimbo ninguém sabe de quando ele é.

Só montagem de imagem, sem tela, para poder ser testado.
"""
import io
import math

from matplotlib import font_manager
from PIL import Image, ImageDraw, ImageFont

# Teto de quadros. Cada um custa ~0,08 s para desenhar e ~68 KB no arquivo: 72 dão ~9 s e ~5 MB,
# que já é um anexo grande. Acima disso o GIF passa a pular de tantas em tantas horas — o carimbo
# de cada quadro deixa o salto à vista.
QUADROS_MAXIMOS = 72
MS_POR_QUADRO = 450   # meio segundo por quadro se lê sem parecer apressado
CORES = 256           # o GIF não vai além disso; abaixo, o degradê do mapa começa a faixear


def passos(paradas: list, maximo: int = QUADROS_MAXIMOS) -> list:
    """As paradas que entram no GIF, pulando de tantas em tantas quando há mais que o teto.

    O salto é sempre inteiro e começa na primeira parada, então o intervalo entre quadros é
    constante — um GIF que pula 1 h aqui e 3 h ali mentiria sobre a velocidade do que se vê.
    """
    if len(paradas) <= maximo:
        return list(paradas)
    salto = math.ceil(len(paradas) / maximo)
    return list(paradas)[::salto]


def intervalo(paradas: list, escolhidas: list) -> int:
    """De quantas em quantas paradas o GIF anda — 1 quando nenhuma foi pulada."""
    return max(1, math.ceil(len(paradas) / len(escolhidas))) if escolhidas else 1


def carimbo(momento, por_dia: bool) -> str:
    """O instante como ele aparece dentro do quadro.

    No diário só a data: escrever "00:00" num mapa que resume o dia inteiro faria pensar que o
    valor é da meia-noite.
    """
    return f"{momento:%d/%m/%Y}" if por_dia else f"{momento:%d/%m/%Y %H:%M}"


def montar(quadros: list[tuple[bytes, str]], ms_por_quadro: int = MS_POR_QUADRO) -> bytes:
    """GIF animado dos PNGs, cada um carimbado com o seu instante."""
    imagens = []
    for png, instante in quadros:
        imagem = Image.open(io.BytesIO(png)).convert("RGB")
        _carimbar(imagem, instante)
        imagens.append(imagem.quantize(colors=CORES, method=Image.MEDIANCUT))

    saida = io.BytesIO()
    imagens[0].save(saida, format="GIF", save_all=True, append_images=imagens[1:],
                    duration=ms_por_quadro, loop=0, optimize=True)
    return saida.getvalue()


def _carimbar(imagem: Image.Image, texto: str) -> None:
    """Escreve o instante no alto à esquerda, sobre uma tarja clara.

    Ali o desenho não chega: em MS e em MT o canto noroeste do enquadramento é vazio, então o
    carimbo não cobre dado nenhum. Vale conferir ao acrescentar um estado cujo contorno encoste
    nesse canto. A tarja existe porque sem ela o texto sumiria sobre as cores claras da escala.
    """
    desenho = ImageDraw.Draw(imagem)
    fonte = _fonte(max(14, imagem.width // 28))
    margem = imagem.width // 40
    esquerda, alto, direita, baixo = desenho.textbbox((margem, margem), texto, font=fonte)
    folga = max(4, fonte.size // 3)
    desenho.rectangle([esquerda - folga, alto - folga, direita + folga, baixo + folga],
                      fill="white", outline="#555555", width=2)
    desenho.text((margem, margem), texto, font=fonte, fill="#111111")


def _fonte(tamanho: int) -> ImageFont.FreeTypeFont:
    """A DejaVu vem dentro do matplotlib, então existe em qualquer instalação — inclusive na
    nuvem, onde não há fonte nenhuma do sistema garantida."""
    return ImageFont.truetype(font_manager.findfont("DejaVu Sans"), tamanho)
